from __future__ import annotations

import json
from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from runs.services.event_contracts import (
    make_approvals_push,
    make_run_push,
    make_workspace_push,
)
from runs.tasks import run_tick as run_tick_task
from tools.services.approvals import approve_tool_call as approve_tool_call_service


def group_run(run_id: str) -> str:
    return f"run.{run_id}"


def group_workspace(workspace_id: str) -> str:
    return f"ws.{workspace_id}"


def group_approvals(workspace_id: str) -> str:
    return f"approvals.{workspace_id}"


class WorkspaceConsumer(AsyncJsonWebsocketConsumer):
    """Workspace-level consumer (dashboard stream and optionally approvals stream).

    Today (pre-migrations):
    - Accept connection
    - Join workspace group (workspace_id passed as querystring ?workspace_id=...)
    - Optionally subscribe/unsubscribe to approvals group via commands
    - Can broadcast test messages via "cmd: ping"

    Tomorrow:
    - Validate workspace membership against DB
    - Push real run/approval events
    """

    workspace_id: Optional[str] = None
    approvals_subscribed: bool = False

    async def connect(self):
        user = self.scope.get("user")

        await self.accept()

        self.workspace_id = self._get_qs_param("workspace_id")
        if not self.workspace_id:
            await self.send_json(
                {
                    "type": "push",
                    "topic": "workspace.event",
                    "event": "warning",
                    "ts": "",
                    "data": {
                        "message": "No workspace_id provided in query string. Example: /ws/ui/workspace/?workspace_id=<uuid>",
                    },
                }
            )
            return

        await self.channel_layer.group_add(
            group_workspace(self.workspace_id),
            self.channel_name,
        )

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
            await self.channel_layer.group_discard(
                group_workspace(self.workspace_id),
                self.channel_name,
            )
        if self.approvals_subscribed:
            await self.channel_layer.group_discard(
                group_approvals(self.workspace_id),
                self.channel_name,
            )

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        msg_type = content.get("type")
        if msg_type != "cmd":
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
        if not self.workspace_id:
            return
        if self.approvals_subscribed:
            return
        await self.channel_layer.group_add(
            group_approvals(self.workspace_id),
            self.channel_name,
        )
        self.approvals_subscribed = True
        await self.send_json(
            make_approvals_push(
                workspace_id=self.workspace_id,
                event="subscribed",
                data={"message": "Subscribed to approvals stream"},
            )
        )

    async def _unsubscribe_approvals(self):
        if not self.workspace_id:
            return
        if not self.approvals_subscribed:
            return
        await self.channel_layer.group_discard(
            group_approvals(self.workspace_id),
            self.channel_name,
        )
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
    """Per-run consumer (run detail page).

    Today (pre-migrations):
    - Join run group run.<run_id>
    - Accept basic commands: ping, request_snapshot (stub), approve_tool_call (stub), cancel_run (stub)
    - Allows us to test group fanout easily.

    Tomorrow:
    - Validate permissions (run in workspace + membership + roles)
    - Perform DB actions on commands (approve/cancel/retry)
    - Provide snapshot + catch-up by seq
    """

    run_id: Optional[str] = None
    workspace_id: Optional[str] = None

    async def connect(self):
        await self.accept()
        self.run_id = self._get_url_kw("run_id")
        if not self.run_id:
            await self.close(code=4400)
            return
        await self.channel_layer.group_add(
            group_run(self.run_id),
            self.channel_name,
        )
        await self.send_json(
            make_run_push(
                run_id=self.run_id,
                event="connected",
                data={"message": "Connected to run stream"},
            )
        )

    async def disconnect(self, close_code):
        if self.run_id:
            await self.channel_layer.group_discard(
                group_run(self.run_id),
                self.channel_name,
            )

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        msg_type = content.get("type")
        if msg_type != "cmd":
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
                        "note": "Snapshot is stubbed until migrations/DB are in place.",
                    },
                )
            )
            return
        if cmd == "approve_tool_call":
            tool_call_id = content.get("tool_call_id")
            user = self.scope.get("user")
            if not tool_call_id:
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "tool_call_id is required"},
                    )
                )
                return
            if not user or not getattr(user, "is_authenticated", False):
                await self.send_json(
                    make_run_push(
                        run_id=self.run_id or "",
                        event="error",
                        data={"message": "Authentication required for approvals"},
                    )
                )
                return

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
        if cmd in {"cancel_run", "retry_run"}:
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
