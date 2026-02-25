from __future__ import annotations

from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from core.models import WorkspaceMembership
from core.services.limits import LimitExceeded, LimitKey, QUOTA_MANAGER
from runs.models import AgentRun
from runs.services.event_contracts import (
    make_approvals_push,
    make_run_push,
    make_workspace_push,
)
from runs.services.recovery import cancel_run, pause_run, resume_run
from runs.tasks import run_tick as run_tick_task
from tools.services.approvals import approve_tool_call as approve_tool_call_service
from runs.services.subruns import spawn_subrun

APPROVAL_ROLES = {
    WorkspaceMembership.Role.OWNER,
    WorkspaceMembership.Role.ADMIN,
    WorkspaceMembership.Role.OPERATOR,
}
CONTROL_ROLES = APPROVAL_ROLES


def group_run(run_id: str) -> str:
    return f"run.{run_id}"


def group_workspace(workspace_id: str) -> str:
    return f"ws.{workspace_id}"


def group_approvals(workspace_id: str) -> str:
    return f"approvals.{workspace_id}"


@database_sync_to_async
def _has_workspace_membership(user_id: int, workspace_id: str) -> bool:
    return WorkspaceMembership.objects.filter(
        workspace_id=workspace_id,
        user_id=user_id,
        is_active=True,
    ).exists()


@database_sync_to_async
def _fetch_run_and_membership(run_id: str, user_id: int) -> tuple[Optional[AgentRun], Optional[WorkspaceMembership]]:
    try:
        run = AgentRun.objects.select_related("workspace").get(id=run_id)
    except AgentRun.DoesNotExist:
        return None, None
    membership = WorkspaceMembership.objects.filter(
        workspace=run.workspace, user_id=user_id, is_active=True
    ).first()
    return run, membership


class WorkspaceConsumer(AsyncJsonWebsocketConsumer):
    """Workspace-level consumer with membership validation."""

    workspace_id: Optional[str] = None
    approvals_subscribed: bool = False
    workspace_conn_acquired: bool = False
    user_conn_acquired: bool = False
    user_id: Optional[int] = None

    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4403)
            return

        self.workspace_id = self._get_qs_param("workspace_id")
        if not self.workspace_id:
            await self.close(code=4400)
            return

        has_access = await _has_workspace_membership(user.id, self.workspace_id)
        if not has_access:
            await self.close(code=4403)
            return

        try:
            QUOTA_MANAGER.acquire_concurrency(
                self.workspace_id, LimitKey.WS_CONNECTIONS_WORKSPACE, self.channel_name
            )
            self.workspace_conn_acquired = True
            QUOTA_MANAGER.acquire_concurrency(
                str(user.id), LimitKey.WS_CONNECTIONS_USER, self.channel_name
            )
            self.user_conn_acquired = True
            self.user_id = user.id
        except LimitExceeded:
            await self.close(code=4408)
            return

        await self.accept()
        await self.channel_layer.group_add(group_workspace(self.workspace_id), self.channel_name)
        await self.send_json(
            make_workspace_push(
                workspace_id=self.workspace_id,
                event="connected",
                data={
                    "user": getattr(user, "username", None),
                    "approvals_subscribed": self.approvals_subscribed,
                },
            )
        )

    async def disconnect(self, close_code):
        if self.workspace_id:
            await self.channel_layer.group_discard(group_workspace(self.workspace_id), self.channel_name)
        if self.approvals_subscribed:
            await self.channel_layer.group_discard(group_approvals(self.workspace_id), self.channel_name)
        if self.workspace_conn_acquired and self.workspace_id:
            QUOTA_MANAGER.release_concurrency(
                self.workspace_id, LimitKey.WS_CONNECTIONS_WORKSPACE, self.channel_name
            )
        if self.user_conn_acquired and self.user_id is not None:
            QUOTA_MANAGER.release_concurrency(
                str(self.user_id), LimitKey.WS_CONNECTIONS_USER, self.channel_name
            )

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        if content.get("type") != "cmd":
            return

        cmd = content.get("cmd")
        if cmd == "subscribe_approvals":
            await self._subscribe_approvals()
        elif cmd == "unsubscribe_approvals":
            await self._unsubscribe_approvals()
        elif cmd == "ping":
            await self.send_json(
                make_workspace_push(
                    workspace_id=self.workspace_id or "",
                    event="pong",
                    data={"message": "pong", "echo": content.get("data", {})},
                )
            )
        else:
            await self.send_json(
                make_workspace_push(
                    workspace_id=self.workspace_id or "",
                    event="error",
                    data={"message": f"Unknown cmd: {cmd}"},
                )
            )

    async def push(self, event: Dict[str, Any]):
        payload = event.get("payload")
        if payload:
            await self.send_json(payload)

    async def _subscribe_approvals(self):
        if not self.workspace_id or self.approvals_subscribed:
            return
        await self.channel_layer.group_add(group_approvals(self.workspace_id), self.channel_name)
        self.approvals_subscribed = True
        await self.send_json(
            make_approvals_push(
                workspace_id=self.workspace_id,
                event="subscribed",
                data={"message": "Subscribed to approvals stream"},
            )
        )

    async def _unsubscribe_approvals(self):
        if not self.workspace_id or not self.approvals_subscribed:
            return
        await self.channel_layer.group_discard(group_approvals(self.workspace_id), self.channel_name)
        self.approvals_subscribed = False
        await self.send_json(
            make_approvals_push(
                workspace_id=self.workspace_id,
                event="unsubscribed",
                data={"message": "Unsubscribed from approvals stream"},
            )
        )

    def _get_qs_param(self, key: str) -> Optional[str]:
        raw = (self.scope.get("query_string") or b"").decode("utf-8", errors="ignore")
        if not raw:
            return None
        parts = raw.split("&")
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k == key and v:
                return v
        return None


class RunConsumer(AsyncJsonWebsocketConsumer):
    """Per-run consumer with run/membership validation and approval enforcement."""

    run_id: Optional[str] = None
    workspace_id: Optional[str] = None
    membership_role: Optional[str] = None
    workspace_conn_acquired: bool = False
    user_conn_acquired: bool = False
    user_id: Optional[int] = None

    async def connect(self):
        user = self.scope.get("user")
        self.run_id = self._get_url_kw("run_id")
        if not self.run_id or not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4403)
            return

        run, membership = await _fetch_run_and_membership(self.run_id, user.id)
        if not run or not membership:
            await self.close(code=4403)
            return

        self.workspace_id = str(run.workspace_id)
        self.membership_role = membership.role

        try:
            QUOTA_MANAGER.acquire_concurrency(
                self.workspace_id, LimitKey.WS_CONNECTIONS_WORKSPACE, self.channel_name
            )
            self.workspace_conn_acquired = True
            QUOTA_MANAGER.acquire_concurrency(
                str(user.id), LimitKey.WS_CONNECTIONS_USER, self.channel_name
            )
            self.user_conn_acquired = True
            self.user_id = user.id
        except LimitExceeded:
            await self.close(code=4408)
            return

        await self.accept()
        await self.channel_layer.group_add(group_run(self.run_id), self.channel_name)
        await self.send_json(
            make_run_push(
                run_id=self.run_id,
                event="connected",
                data={"message": "Connected to run stream"},
            )
        )

    async def disconnect(self, close_code):
        if self.run_id:
            await self.channel_layer.group_discard(group_run(self.run_id), self.channel_name)
        if self.workspace_conn_acquired and self.workspace_id:
            QUOTA_MANAGER.release_concurrency(
                self.workspace_id, LimitKey.WS_CONNECTIONS_WORKSPACE, self.channel_name
            )
        if self.user_conn_acquired and self.user_id is not None:
            QUOTA_MANAGER.release_concurrency(
                str(self.user_id), LimitKey.WS_CONNECTIONS_USER, self.channel_name
            )

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        if content.get("type") != "cmd":
            return
        cmd = content.get("cmd")
        if cmd == "ping":
            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event="pong",
                    data={"message": "pong", "echo": content.get("data", {})},
                )
            )
            return
        if cmd == "request_snapshot":
            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event="snapshot",
                    data={
                        "run": {"id": self.run_id, "status": "UNKNOWN"},
                        "steps": [],
                        "events_since_seq": [],
                        "note": "Snapshot is stubbed until DB is ready.",
                    },
                )
            )
            return
        if cmd == "approve_tool_call":
            if self.membership_role not in APPROVAL_ROLES:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "Insufficient role for approvals"},
                    )
                )
                return

            tool_call_id = content.get("tool_call_id")
            if not tool_call_id:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "tool_call_id is required"},
                    )
                )
                return

            user = self.scope.get("user")
            try:
                tool_call = await database_sync_to_async(
                    approve_tool_call_service,
                    thread_sensitive=True,
                )(tool_call_id=tool_call_id, user=user)
            except Exception as exc:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": str(exc)},
                    )
                )
                return

            run_tick_task.delay(str(tool_call.run_id))
            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event="tool_call_approval_ack",
                    data={"tool_call_id": str(tool_call.id)},
                )
            )
            return
        if cmd in {"cancel_run", "pause_run", "resume_run"}:
            if self.membership_role not in CONTROL_ROLES:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "Insufficient role for run control"},
                    )
                )
                return

            handler = {
                "cancel_run": cancel_run,
                "pause_run": pause_run,
                "resume_run": resume_run,
            }[cmd]

            params = {}
            if cmd == "cancel_run":
                params["reason"] = content.get("reason")

            try:
                run_obj = await database_sync_to_async(handler, thread_sensitive=True)(
                    run_id=self.run_id,
                    **{k: v for k, v in params.items() if v is not None},
                )
            except Exception as exc:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": str(exc)},
                    )
                )
                return

            if cmd == "resume_run":
                run_tick_task.delay(str(self.run_id))

            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event=f"{cmd}_ack",
                    data={"status": run_obj.status, "run_id": str(run_obj.id)},
                )
            )
            return
        if cmd == "spawn_subrun":
            if self.membership_role not in CONTROL_ROLES:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "Insufficient role for run control"},
                    )
                )
                return

            prompt = content.get("input_text")

            try:
                spawn_options = content.get("options", {})
                spawn_kwargs = {"parent_run_id": self.run_id, "input_text": prompt}
                for key in {
                    "join_policy",
                    "quorum",
                    "timeout_seconds",
                    "failure_policy",
                    "group_id",
                    "metadata",
                }:
                    if key in spawn_options:
                        spawn_kwargs[key] = spawn_options[key]

                child = await database_sync_to_async(
                    spawn_subrun,
                    thread_sensitive=True,
                )(**spawn_kwargs)
            except Exception as exc:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": str(exc)},
                    )
                )
                return

            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event="spawn_subrun_ack",
                    data={"child_run_id": str(child.id)},
                )
            )
            return
        if cmd == "retry_run":
            await self.send_json(
                make_run_push(
                    run_id=self.run_id or "",
                    event="cmd_received",
                    data={"cmd": cmd, "payload": content},
                )
            )
            return
        await self.send_json(
            make_run_push(
                run_id=self.run_id or "",
                event="error",
                data={"message": f"Unknown cmd: {cmd}"},
            )
        )

    async def push(self, event: Dict[str, Any]):
        payload = event.get("payload")
        if payload:
            await self.send_json(payload)

    def _get_url_kw(self, key: str) -> Optional[str]:
        return self.scope.get("url_route", {}).get("kwargs", {}).get(key)
