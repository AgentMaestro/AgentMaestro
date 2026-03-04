import asyncio
import json
import logging
import os

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from agents.models import Agent
from agents.utils import build_transport_status
from core.models import WorkspaceMembership
from llm.models import LLMModelProfile
from llm.services.providers.openai_ws import (
    OpenAIResponsesWSException,
    OpenAIResponsesWSPreviousResponseNotFound,
)
from llm.services.registry import get_client
from llm.system_context import build_system_context
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

logger = logging.getLogger(__name__)


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


@database_sync_to_async
def _assert_tool_allowed(agent: Agent, user, tool_name: str):
    return assert_tool_allowed(agent, user, tool_name)


@database_sync_to_async
def _create_agent_run(agent: Agent, user, started_at: timezone.datetime):
    return AgentRun.objects.create(
        workspace=agent.workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        started_at=started_at,
        input_text="",
    )


@database_sync_to_async
def _build_transport_status(agent: Agent):
    return build_transport_status(agent)


@database_sync_to_async
def _get_effective_tools(agent: Agent, user):
    return get_effective_tools(agent, user)


class AgentChatConsumer(AsyncJsonWebsocketConsumer):
    agent: Agent | None = None
    session = None
    client = None
    run_id: str | None = None
    model_name: str = ""
    transport: str = "http"
    use_ws: bool = False
    history: list[dict[str, str]] = []
    session_tools: list[dict[str, object]] = []
    tool_definitions: list[dict[str, object]] = []
    run: AgentRun | None = None
    workspace_group: str | None = None
    approvals_group: str | None = None
    _tool_call_waiters: dict[str, asyncio.Future] = {}
    _include_system_context = True

    async def _log_transport_traffic(self, label: str, data: object | None):
        if not data:
            return
        try:
            payload_text = json.dumps(data, ensure_ascii=False, default=str)
        except Exception:
            payload_text = str(data)
        await self.send_json(
            {
                "type": "system",
                "text": f"[{label}] {payload_text}",
                "timestamp": timezone.now().isoformat(),
            }
        )

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
        run = await _create_agent_run(agent, user, timezone.now())
        self.run = run
        self.run_id = str(run.id)
        profile = await _get_profile(agent.policy_name)
        provider = profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
        model_name = profile.model if profile else agent.default_model
        self.client = get_client(provider)
        self.model_name = model_name
        self.transport = os.getenv(
            "OPENAI_TRANSPORT", getattr(self.client, "transport", "http")
        ).lower()
        self.use_ws = self.transport == "ws"
        logger.info(
            "Opening OpenAI %s session for agent=%s run=%s model=%s provider=%s transport=%s",
            "WS" if self.use_ws else "HTTP",
            agent.slug,
            self.run_id,
            model_name,
            provider,
            self.transport,
        )
        self.session = None
        if self.use_ws:
            await self.client.cleanup_ws_sessions()
            try:
                self.session = await self.client.get_ws_session(
                    self.run_id, model_name, agent_id=str(agent.id)
                )
            except Exception as exc:
                logger.error(
                    "OpenAI WS session error for agent %s: %s", agent.slug, exc, exc_info=True
                )
                await self.close(code=1011)
                return
            logger.info(
                "OpenAI WS session established for agent=%s run=%s model=%s",
                agent.slug,
                self.run_id,
                model_name,
            )
        else:
            logger.info(
                "OpenAI HTTP transport ready for agent=%s run=%s model=%s",
                agent.slug,
                self.run_id,
                model_name,
            )

        effective_tools = await _get_effective_tools(agent, user)
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

        self.tool_definitions = tool_payloads
        if tool_payloads and self.use_ws:
            self.session_tools = self.client.format_tool_definitions_for_responses(tool_payloads)
        else:
            self.session_tools = []

        tool_names = [
            entry.get("name") for entry in self.tool_definitions if entry.get("name")
        ]
        self.history = []
        self.system_context = build_system_context(
            agent,
            model_name=model_name,
            transport=self.transport,
            tool_names=tool_names,
        )
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
        transport_status = await _build_transport_status(agent)
        await self.send_json(
            {
                "type": "connected",
                "system_context": self.system_context or "",
                "tools": self.tools_meta,
                "transport_status": transport_status,
                "model": model_name,
            }
        )
        transport_detail = "WS" if self.use_ws else "HTTP"
        system_detail = f"OpenAI {transport_detail} connected ({model_name})"
        await self.send_json(
            {
                "type": "system",
                "text": system_detail,
                "timestamp": timezone.now().isoformat(),
            }
        )

    async def disconnect(self, code):
        if self.use_ws and self.client and self.run_id:
            agent_slug = self.agent.slug if self.agent else "unknown"
            try:
                await self.client.close_ws_session(self.run_id, model=self.model_name)
                logger.info(
                    "Closed OpenAI WS session for agent=%s run=%s", agent_slug, self.run_id
                )
            except Exception as exc:
                logger.warning(
                    "Failed to close OpenAI WS session for agent=%s run=%s: %s",
                    agent_slug,
                    self.run_id,
                    exc,
                    exc_info=True,
                )
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
        if message_type == "tool_disconnect":
            user = self.scope.get("user")
            user_label = getattr(user, "username", "unknown")
            await self._log_transport_traffic(
                "tool_disconnect",
                {"user": user_label, "run_id": self.run_id, "agent": self.agent.slug if self.agent else "unknown"},
            )
            await self.send_json(
                {
                    "type": "system",
                    "text": f"Disconnect requested by {user_label}. Closing OpenAI session.",
                    "timestamp": timezone.now().isoformat(),
                }
            )
            logger.info("Disconnect requested via WS for agent=%s run=%s", user_label, self.run_id)
            # TODO: integrate tool_disconnect events with the approvals/channel layer if needed.
            await self.close(code=1000)
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
        async with self._send_lock:
            if self.use_ws:
                await self._dispatch_to_provider_ws()
            else:
                await self._dispatch_to_provider_http()

    async def _dispatch_to_provider_ws(self):
        if not self.session:
            return
        tools = self.session_tools if self.session_tools else None
        reconnect_attempts = 0
        max_reconnects = 1
        while True:
            input_items = self._build_ws_input_items()
            response_payload = {
                "model": self.session.model if self.session else "unknown",
                "input": input_items,
            }
            if tools:
                response_payload["tools"] = tools
            payload_snapshot: dict[str, object] = {
                "type": "response.create",
                "model": response_payload["model"],
                "store": False,
                "input": response_payload["input"],
            }
            if tools:
                payload_snapshot["tools"] = tools
            previous_id = getattr(self.session, "previous_response_id", None)
            if previous_id:
                payload_snapshot["previous_response_id"] = previous_id
            await self._log_transport_traffic("WS Send", payload_snapshot)
            try:
                response = await self.session.create_or_continue(
                    input_items=input_items,
                    tools=tools,
                )
                await self._log_transport_traffic(
                    "WS Rcv",
                    response.get("raw") if isinstance(response, dict) else response,
                )
            except OpenAIResponsesWSPreviousResponseNotFound as exc:
                if self.session:
                    self.session.previous_response_id = None
                await self._handle_ws_failure(exc, input_items, summary=True)
                if reconnect_attempts >= max_reconnects:
                    await self._send_error_and_abort(exc)
                    return
                reconnect_attempts += 1
                continue
            except OpenAIResponsesWSException as exc:
                await self._handle_ws_failure(exc, input_items, summary=True)
                if reconnect_attempts >= max_reconnects:
                    await self._send_error_and_abort(exc)
                    return
                reconnect_attempts += 1
                continue

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
                return

    async def _dispatch_to_provider_http(self):
        tools = self.tool_definitions if self.tool_definitions else None
        model = self.model_name or "unknown"
        while True:
            snapshot_messages = [
                {"role": entry.get("role"), "content": (entry.get("content") or "")[:160]}
                for entry in self.history[-4:]
            ]
            payload_snapshot: dict[str, object] = {"model": model, "messages": snapshot_messages}
            if tools:
                payload_snapshot["tools"] = [entry.get("name") for entry in tools if entry.get("name")]
            await self._log_transport_traffic("HTTP SEND", payload_snapshot)
            try:
                response = await self.client.complete(
                    self.history,
                    model=model,
                    tools=tools,
                )
            except Exception as exc:
                await self._send_http_error(exc)
                return
            await self._log_transport_traffic(
                "HTTP RCV",
                {
                    "model": model,
                    "response_id": response.get("response_id"),
                    "text": (response.get("text") or "")[:400],
                },
            )
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
                return

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
            entry = await self._find_effective_tool(tool_name)
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
        tool_entry: dict[str, object] = {"role": "tool", "content": tool_text}
        if call_id:
            tool_entry["tool_call_id"] = call_id
        self.history.append(tool_entry)
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

    async def _find_effective_tool(self, tool_name: str):
        agent = self.agent
        user = self.scope.get("user")
        return await _assert_tool_allowed(agent, user, tool_name)

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
            if role == "system" and not self._include_system_context:
                continue
            content_item = {"type": "output_text", "text": content} if role == "assistant" else {"type": "input_text",
                                                                                                 "text": content}
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [content_item],
                }
            )
        return items

    def _build_ws_input_items(self) -> list[dict[str, object]]:
        previous_id = getattr(self.session, "previous_response_id", None)
        if not previous_id:
            return self._build_input_items()
        last_user = self._last_user_message()
        if last_user:
            return [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": last_user}],
                }
            ]
        return self._build_input_items()

    def _last_user_message(self) -> str | None:
        for entry in reversed(self.history):
            if entry.get("role") != "user":
                continue
            content = (entry.get("content") or "").strip()
            if content:
                return content
        return None

    def _summarize_input_items(self, input_items: list[dict[str, object]]) -> str:
        snippets: list[str] = []
        for item in input_items[-3:]:
            role = item.get("role")
            content_items = item.get("content", [])
            if isinstance(content_items, list):
                text = " ".join(
                    str(entry.get("text") or "")
                    for entry in content_items
                    if isinstance(entry, dict)
                ).strip()
            else:
                text = str(content_items or "").strip()
            if not text:
                continue
            snippets.append(f"{role}: {text}")
        return " | ".join(snippets) if snippets else "no input captured"

    async def _handle_ws_failure(
            self,
            exc: Exception,
            input_items: list[dict[str, object]],
            *,
            summary: bool = False,
    ) -> None:
        summary_text = self._summarize_input_items(input_items) if summary else ""
        logger.warning(
            "OpenAI WS reconnect triggered run=%s agent=%s reason=%s summary=%s",
            self.run_id,
            self.agent.slug if self.agent else "unknown",
            exc,
            summary_text,
        )
        await self.send_json(
            {
                "type": "system",
                "text": f"OpenAI WS connection hiccup: {exc}. Reconnecting…",
                "timestamp": timezone.now().isoformat(),
            }
        )
        if summary_text:
            await self.send_json(
                {
                    "type": "system",
                    "text": f"Last context: {summary_text}",
                    "timestamp": timezone.now().isoformat(),
                }
            )

    async def _send_error_and_abort(self, exc: Exception) -> None:
        logger.error("OpenAI WS giving up after retries for run=%s: %s", self.run_id, exc)
        await self.send_json(
            {
                "type": "error",
                "message": str(exc),
                "timestamp": timezone.now().isoformat(),
            }
        )

    async def _send_http_error(self, exc: Exception) -> None:
        logger.error("OpenAI HTTP call failed for run=%s agent=%s: %s", self.run_id,
                     self.agent.slug if self.agent else "unknown", exc)
        await self.send_json(
            {
                "type": "error",
                "message": str(exc),
                "timestamp": timezone.now().isoformat(),
            }
        )
