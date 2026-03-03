import asyncio
import json

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from agents.models import Agent
from agents.utils import build_transport_status
from core.models import WorkspaceMembership
from llm.models import LLMModelProfile
from llm.services.registry import get_client
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.steps import append_step
from tools.models import ToolCall
from tools.policy import ToolNotAllowedError, assert_tool_allowed, get_effective_tools
from tools.services.approvals import (
    approve_tool_call,
    deny_tool_call,
    request_tool_call_approval,
    TOOL_CALL_DENIED_EVENT,
    TOOL_CALL_STATUS_EVENT,
)


def _run_group(run_id: str) -> str:
    return f"run.{run_id}"


def _approvals_group(workspace_id: str) -> str:
    return f"approvals.{workspace_id}"


@database_sync_to_async
def _fetch_agent(slug: str) -> Agent:
    return Agent.objects.select_related("workspace", "owner").get(slug=slug)


@database_sync_to_async
def _has_workspace_access(user_id: int, agent: Agent) -> bool:
    if agent.owner_id == user_id:
        return True
    return WorkspaceMembership.objects.filter(
        workspace=agent.workspace, user_id=user_id, is_active=True
    ).exists()


@database_sync_to_async
def _get_profile(policy_name: str | None) -> LLMModelProfile | None:
    if not policy_name:
        return None
    return (
        LLMModelProfile.objects.filter(name=policy_name, is_active=True)
        .order_by("name")
        .first()
    )


@database_sync_to_async
def _record_denied_tool_call(
    *,
    run_id: str,
    tool_name: str,
    args: dict[str, object],
    provider_call_id: str,
    reason: str,
) -> str:
    run = (
        AgentRun.objects.select_related("workspace")
        .get(id=run_id)
    )
    step = append_step(
        run_id=run_id,
        kind=AgentStep.Kind.TOOL_CALL,
        payload={"tool_name": tool_name, "args": args},
    )
    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name=tool_name,
        args=args,
        requires_approval=False,
        status=ToolCall.Status.DENIED,
        correlation_id=step.correlation_id,
        provider_call_id=provider_call_id,
        error=reason,
        observed_at=timezone.now(),
    )
    append_event(
        run_id=run_id,
        event_type=TOOL_CALL_STATUS_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "status": tool_call.status,
            "args": tool_call.args,
            "requires_approval": tool_call.requires_approval,
            "error": reason,
        },
        correlation_id=step.correlation_id,
    )
    return str(tool_call.id)


@database_sync_to_async
def _update_provider_metadata(tool_call_id: str, call_id: str) -> None:
    ToolCall.objects.filter(id=tool_call_id).update(provider_call_id=call_id)


class AgentChatConsumer(AsyncJsonWebsocketConsumer):
    agent: Agent | None = None
    session = None
    client = None
    run_id: str | None = None
    history: list[dict[str, str]] = []
    session_tools: list[dict[str, object]] = []
    run: AgentRun | None = None
    workspace_group: str | None = None
    approvals_group: str | None = None
    _tool_call_waiters: dict[str, asyncio.Future] = {}

    async def connect(self):
        user = self.scope.get("user")
        slug = self.scope.get("url_route", {}).get("kwargs", {}).get("slug")
        if not user or not getattr(user, "is_authenticated", False) or not slug:
            await self.close(code=4403)
            return

        try:
            agent = await _fetch_agent(slug)
        except Agent.DoesNotExist:
            await self.close(code=4404)
            return

        access = await _has_workspace_access(user.id, agent)
        if not access:
            await self.close(code=4403)
            return

        self.agent = agent
        workspace_id = str(agent.workspace_id)
        run = AgentRun.objects.create(
            workspace=agent.workspace,
            agent=agent,
            started_by=user,
            status=AgentRun.Status.RUNNING,
            channel=AgentRun.Channel.DASHBOARD,
            started_at=timezone.now(),
            input_text="",
        )
        self.run = run
        self.run_id = str(run.id)
        profile = await _get_profile(agent.policy_name)
        provider = profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
        model_name = profile.model if profile else agent.default_model
        self.client = get_client(provider)
        await self.client.cleanup_ws_sessions()
        self.session = await self.client.get_ws_session(self.run_id, model_name)
        self.session_tools = []

        effective_tools = get_effective_tools(agent, user)
        self.tools_meta = [
            {
                "name": entry.tool.name,
                "description": entry.description,
                "risk": entry.risk,
                "requires_approval": entry.requires_approval,
            }
            for entry in effective_tools
        ]

        tool_payloads = []
        for entry in effective_tools:
            tool_payloads.append(
                {
                    "name": entry.tool.name,
                    "description": entry.description,
                    "parameters": entry.args_schema or {},
                }
            )

        if tool_payloads:
            self.session_tools = self.client.format_tool_definitions_for_responses(tool_payloads)

        self.history = []
        self.system_context = self._build_system_context(agent)
        if self.system_context:
            self.history.append({"role": "system", "content": self.system_context})

        self._send_lock = asyncio.Lock()
        self._tool_call_waiters = {}
        await self.accept()
        channel_layer = self.channel_layer
        if channel_layer:
            run_group = _run_group(self.run_id)
            approvals_group = _approvals_group(workspace_id)
            await channel_layer.group_add(run_group, self.channel_name)
            await channel_layer.group_add(approvals_group, self.channel_name)
            self.workspace_group = run_group
            self.approvals_group = approvals_group
        await self.send_json(
            {
                "type": "connected",
                "system_context": self.system_context or "",
                "tools": self.tools_meta,
                "transport_status": build_transport_status(agent),
                "model": model_name,
            }
        )

    async def disconnect(self, code):
        if self.client and self.run_id:
            try:
                await self.client.close_ws_session(self.run_id)
            except Exception:
                pass
        channel_layer = self.channel_layer
        if channel_layer:
            if self.workspace_group:
                await channel_layer.group_discard(self.workspace_group, self.channel_name)
            if self.approvals_group:
                await channel_layer.group_discard(self.approvals_group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        message_type = content.get("type")
        if message_type == "chat.message":
            text = (content.get("text") or "").strip()
            if not text:
                return
            self.history.append({"role": "user", "content": text})
            await self._dispatch_to_provider()
            return
        if message_type == "tool_approve":
            tool_call_id = content.get("tool_call_id")
            if tool_call_id:
                await sync_to_async(approve_tool_call)(
                    tool_call_id=tool_call_id, user=self.scope.get("user")
                )
            return
        if message_type == "tool_deny":
            tool_call_id = content.get("tool_call_id")
            reason = content.get("reason")
            if tool_call_id:
                await sync_to_async(deny_tool_call)(
                    tool_call_id=tool_call_id, user=self.scope.get("user"), reason=reason
                )
            return

    async def _dispatch_to_provider(self):
        if not self.session:
            return
        async with self._send_lock:
            tools = self.session_tools if self.session_tools else None
            while True:
                input_items = self._build_input_items()
                try:
                    response = await self.session.create_or_continue(
                        input_items=input_items,
                        tools=tools,
                    )
                except Exception as exc:
                    await self.send_json(
                        {
                            "type": "error",
                            "message": str(exc),
                            "timestamp": timezone.now().isoformat(),
                        }
                    )
                    return

                tool_calls = response.get("tool_calls") or []
                if tool_calls:
                    for call in tool_calls:
                        await self._handle_tool_call(call)
                    continue

                assistant_text = response.get("text") or ""
                if assistant_text:
                    self.history.append({"role": "assistant", "content": assistant_text})
                    await self.send_json(
                        {
                            "type": "message",
                            "role": "assistant",
                            "text": assistant_text,
                            "timestamp": timezone.now().isoformat(),
                        }
                    )
                break

    async def _handle_tool_call(self, call: dict[str, object]):
        tool_name = (call.get("name") or "").strip()
        if not tool_name:
            await self.send_json(
                {
                    "type": "tool_error",
                    "message": "Tool call missing name",
                }
            )
            return

        call_id = str(call.get("call_id") or call.get("id") or call.get("tool_call_id") or "")
        args = self._parse_tool_args(call.get("arguments"))
        try:
            entry = self._find_effective_tool(tool_name)
        except ToolNotAllowedError as exc:
            denied_id = await _record_denied_tool_call(
                run_id=str(self.run_id),
                tool_name=tool_name,
                args=args,
                provider_call_id=call_id,
                reason=str(exc),
            )
            await self.send_json(
                {
                    "type": "tool_denied",
                    "tool_call_id": denied_id,
                    "tool_name": tool_name,
                    "message": str(exc),
                }
            )
            return

        requires_approval = entry.requires_approval
        tool_call = await sync_to_async(request_tool_call_approval)(
            run_id=str(self.run_id),
            tool_name=tool_name,
            args=args,
            requires_approval=requires_approval,
        )
        if call_id:
            await _update_provider_metadata(str(tool_call.id), call_id)
        future = asyncio.get_running_loop().create_future()
        self._tool_call_waiters[str(tool_call.id)] = future
        await self.send_json(
            {
                "type": "tool_request",
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_name,
                "requires_approval": requires_approval,
                "risk": entry.risk,
                "args": args,
                "status": tool_call.status,
            }
        )
        result = await future
        tool_output = result.get("result") or {}
        tool_text = json.dumps(tool_output, ensure_ascii=False)
        self.history.append({"role": "tool", "content": tool_text})
        await self.send_json(
            {
                "type": "tool_result",
                "tool_call_id": result.get("tool_call_id"),
                "tool_name": tool_name,
                "status": result.get("status"),
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "result": tool_output,
            }
        )

    def _parse_tool_args(self, payload: object | None) -> dict[str, object]:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
        return {"value": payload}

    def _find_effective_tool(self, tool_name: str):
        agent = self.agent
        user = self.scope.get("user")
        return assert_tool_allowed(agent, user, tool_name)

    async def push(self, event: dict):
        payload = event.get("payload")
        if not payload:
            return
        event_type = payload.get("event")
        data = payload.get("data") or {}
        if event_type == "tool_call_completed":
            tool_call_id = data.get("tool_call_id")
            if tool_call_id:
                waiter = self._tool_call_waiters.pop(tool_call_id, None)
                if waiter and not waiter.done():
                    waiter.set_result(data)
            await self.send_json({"type": "tool_call_completed", "data": data})
            return
        if event_type == TOOL_CALL_STATUS_EVENT:
            await self.send_json({"type": "tool_status", "data": data})
            return
        if event_type == TOOL_CALL_DENIED_EVENT:
            tool_call_id = data.get("tool_call_id")
            if tool_call_id:
                waiter = self._tool_call_waiters.pop(tool_call_id, None)
                if waiter and not waiter.done():
                    waiter.set_result(
                        {
                            "tool_call_id": tool_call_id,
                            "status": ToolCall.Status.DENIED,
                            "stdout": "",
                            "stderr": "",
                            "result": {"error": data.get("error")},
                        }
                    )
            await self.send_json({"type": "tool_denied", "data": data})
            return
            await self.send_json({"type": "tool_denied", "data": data})

    def _build_input_items(self):
        items: list[dict[str, object]] = []
        for entry in self.history:
            role = entry.get("role")
            content = (entry.get("content") or "").strip()
            if not content or role not in {"system", "user", "assistant"}:
                continue
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        return items

    def _build_system_context(self, agent: Agent) -> str:
        fragments = []
        if agent.soul:
            fragments.append(agent.soul.strip())
        if agent.policy_name:
            fragments.append(f"Policy: {agent.policy_name}.")
        return " ".join(fragments).strip()
