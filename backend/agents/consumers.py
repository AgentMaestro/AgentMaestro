import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

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
from runs.services.recovery import cancel_run, pause_run, resume_run
from scrubadub import Scrubber
from runs.services.event_builders import (
    build_assistant_message_payload,
    build_chat_message_payload,
)
from runs.services.events import append_event
from runs.services.steps import append_step
from runs.services.input_items import build_input_items
from tools.models import ToolCall
from tools.policy import ToolNotAllowedError, assert_tool_allowed, get_effective_tools
from tools.services.approval_grants import active_grants_for_run
from tools.services.command_guardrails import ToolCommandGuardrailError
from tools.services.approvals import (
    approve_tool_call,
    clear_tool_approval_grants,
    deny_tool_call,
    request_tool_call_approval,
    revoke_tool_approval_grant,
    grant_options_for_tool_call,
    TOOL_CALL_DENIED_EVENT,
    TOOL_APPROVAL_GRANTS_UPDATED_EVENT,
    TOOL_CALL_STATUS_EVENT,
)
from tools.services.result_bus import pop_pending_tool_results
from comms.services.agent_chat_bridge import send_run_transport_message

logger = logging.getLogger(__name__)
SCRUBBER = Scrubber()
DEBUG_GROUP_ECHO_EVENT = "debug_group_echo"
DEBUG_GROUP_ECHO_FROM_EVENTS = "debug_group_echo_from_events"


def _should_scrub_prompts() -> bool:
    should_scrub = getattr(settings, "SCRUB_PROMPTS", True)
    if getattr(settings, "TESTING", False) and not getattr(settings, "SCRUB_PROMPTS_FOR_TESTS", False):
        return False
    return should_scrub


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


def _scrub_input_text(text: str | None) -> tuple[str, list[str]]:
    if text is None:
        return "", []
    filths = list(SCRUBBER.iter_filth(text))
    if not filths:
        return text, []
    sanitized = SCRUBBER.clean(text)
    types: list[str] = []
    for filth in filths:
        type_name = getattr(filth, "filth_type", None) or getattr(filth, "type", None)
        if not type_name:
            type_name = filth.__class__.__name__
        types.append(type_name)
    return sanitized, types


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
def _fetch_agent_run(agent: Agent, user, run_id: uuid.UUID) -> AgentRun | None:
    try:
        return AgentRun.objects.get(id=run_id, agent=agent, started_by=user)
    except AgentRun.DoesNotExist:
        return None


@database_sync_to_async
def _get_run_status(run_id: str) -> str | None:
    return AgentRun.objects.filter(id=run_id).values_list("status", flat=True).first()


@database_sync_to_async
def _build_transport_status(agent: Agent):
    return build_transport_status(agent)


@database_sync_to_async
def _get_effective_tools(agent: Agent, user):
    return get_effective_tools(agent, user)


@database_sync_to_async
def _get_active_tool_approval_grants(run_id: str):
    return active_grants_for_run(run_id)


@database_sync_to_async
def _persist_chat_history_event(
    run_id: str,
    role: str,
    text: str,
    *,
    model: str | None = None,
    provider_response_id: str | None = None,
    step_index: int | None = None,
) -> None:
    if not run_id or not text:
        return
    if role == "assistant":
        payload = build_assistant_message_payload(
            text,
            model=model,
            provider_response_id=provider_response_id,
            step_index=step_index,
        )
        event_type = "assistant_message"
    else:
        payload = build_chat_message_payload(role, text)
        event_type = "chat_message"

    append_event(
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        broadcast_to_run=False,
    )


def _normalize_repo_tree_args(args: dict[str, object]) -> dict[str, object]:
    if args is None:
        return {}

    normalized = dict(args)

    logger.info(f"Repo_tree args are:  {normalized}")

    path_val = normalized.pop("path", None)
    abs_val = normalized.pop("absolute_root", None)

    candidate = None
    source = "path"

    if path_val:
        candidate = str(path_val).strip()
        source = "path"
    elif abs_val:
        candidate = str(abs_val).strip()
        source = "absolute_root"
    else:
        candidate = "."

    candidate_path = Path(candidate)

    if source == "path":
        normalized["path"] = candidate_path
        logger.info(f"repo_tree path is {normalized["path"]}")

    elif source == "absolute_root":
        logger.info("repo_tree source is an absolute_root")
        normalized["_warn_repo_tree_path"] = (
            "repo_tree accepts an absolute path, but please send the 'path' argument so we can"
            " canonicalize it for you."
        )
    else:
        logger.info("path or absolute root not given....return '.' for path")

    return normalized


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
    _tool_warnings: dict[str, str] = {}
    _connected: bool = False
    _include_system_context = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tools_meta = None
        self._send_lock = asyncio.Lock()
        self._tool_result_event = asyncio.Event()
        self._tool_result_event.set()
        self._pending_tool_call_id: str | None = None
        self._pending_provider_call_id: str | None = None
        self._last_tool_call_id: str | None = None
        self._last_provider_call_id: str | None = None
        self._tool_output_payload: dict[str, object] | None = None
        self._awaiting_tool_output = False

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
        logger.info(
            "WS CONNECT run=%s consumer=%s channel_name=%s",
            getattr(self, "run_id", None),
            id(self),
            getattr(self, "channel_name", None),
        )
        user = self.scope.get("user")
        logger.info("WS connect START user=%s", getattr(user, "id", None))
        slug = self.scope.get("url_route", {}).get("kwargs", {}).get("slug")

        if not user or not getattr(user, "is_authenticated", False) or not slug:
            logger.info("Connect exited due to missing auth/slug", extra={"user": str(user), "slug": slug})
            await self.close(code=4403)
            return

        try:
            agent = await _fetch_agent(slug)
        except Agent.DoesNotExist:
            logger.info("Connect exited because agent does not exist", extra={"slug": slug})
            await self.close(code=4404)
            return

        access = await _has_workspace_access(user.id, agent)

        if not access:
            logger.info(
                "Connect exited because workspace access denied",
                extra={"user_id": user.id, "agent": agent.slug},
            )
            await self.close(code=4403)
            return

        self.agent = agent
        workspace_id = str(agent.workspace_id)

        requested_run_id = None
        try:
            qs = parse_qs((self.scope.get("query_string") or b"").decode("utf-8"))
            requested_run_id = (qs.get("run") or qs.get("run_id") or [None])[0]
        except Exception as exc:
            logger.debug("Error in finding requested_run_id %s", exc)
            requested_run_id = None

        run = None

        logger.info(
            "WS connect parsed requested_run_id=%s raw_qs=%s",
            requested_run_id,
            (self.scope.get("query_string") or b"").decode("utf-8", errors="ignore"),
        )

        if requested_run_id:
            try:
                run_uuid = uuid.UUID(str(requested_run_id))
                run = await _fetch_agent_run(agent, user, run_uuid)
                if run:
                    logger.info(
                        "WS reusing existing run from querystring",
                        extra={"agent": str(agent.id), "run": str(run.id)},
                    )
            except Exception as e:
                logger.exception(f"Exception while reusing run from querystring:  {e}")
                run = None

        if run is None:
            try:
                run = await _create_agent_run(agent, user, timezone.now())
                logger.info(
                    "WS created new run",
                    extra={"agent": str(agent.id), "run": str(run.id)},
                )
            except Exception as e:
                logger.exception(f"Exception while creating new run: {e}")

        self.run = run
        self.run_id = str(run.id)
        layer = getattr(self, "channel_layer", None)
        logger.info(
            "WS connect channel_layer=%s channel_name=%s run_id=%s",
            layer.__class__.__name__ if layer else None,
            getattr(self, "channel_name", None),
            self.run_id,
        )
        profile = await _get_profile(agent.policy_name)
        provider = profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
        model_name = profile.model if profile else agent.default_model
        self.client = get_client(provider)
        self.model_name = model_name
        self.transport = os.getenv(
            "OPENAI_TRANSPORT", getattr(self.client, "transport", "http")
        ).lower()
        self.use_ws = self.transport == "ws"
        logger.debug(
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

        self.history = []
        self._ensure_system_context()

        run_group = _run_group(self.run_id)
        approvals_group = _approvals_group(workspace_id)
        self.workspace_group = run_group
        self.approvals_group = approvals_group

        if self.channel_layer:
            try:
                layer = self.channel_layer
                logger.info(
                    "AgentChatConsumer.connect DIAG run=%s channel_name=%s layer=%s run_group=%s approvals_group=%s",
                    str(self.run_id),
                    getattr(self, "channel_name", None),
                    layer.__class__.__name__ if layer else None,
                    run_group,
                    approvals_group,
                )
                hosts = getattr(layer, "hosts", None) or getattr(layer, "_hosts", None)
                logger.info(
                    "AgentChatConsumer.connect DIAG layer_hosts=%s layer_repr=%s",
                    hosts,
                    repr(layer),
                )
            except Exception:
                logger.exception("AgentChatConsumer.connect DIAG logging failed")
            await self.channel_layer.group_add(run_group, self.channel_name)
            await self.channel_layer.group_add(approvals_group, self.channel_name)
            logger.info(
                "Channel group_add run_group=%s approvals_group=%s consumer=%s",
                run_group,
                approvals_group,
                id(self),
            )
            logger.info(
                "WS group_add OK run_group=%s approvals_group=%s",
                run_group,
                approvals_group,
            )
            try:
                echo_payload = {
                    "ts": time.time(),
                    "run_id": str(self.run_id),
                    "channel_name": getattr(self, "channel_name", None),
                    "run_group": run_group,
                }
                logger.info("AgentChatConsumer.connect DIAG sending debug_group_echo to %s", run_group)
                await self.channel_layer.group_send(
                    run_group,
                    {
                        "type": "push",
                        "payload": {
                            "type": "push",
                            "topic": "run.event",
                            "event": "debug_group_echo",
                            "data": echo_payload,
                            "run_id": str(self.run_id),
                        },
                    },
                )
            except Exception:
                logger.exception("AgentChatConsumer.connect DIAG debug_group_echo send failed")

        await self.accept()
        self._connected = True
        transport_status = await _build_transport_status(agent)
        await self.send_json(
            {
                "type": "connected",
                "system_context": self.system_context or "",
                "tools": self.tools_meta,
                "transport_status": transport_status,
                "run_id": self.run_id,
                "model": model_name,
                "approval_grants": await _get_active_tool_approval_grants(self.run_id),
                "run_status": run.status,
            }
        )
        transport_detail = "WS" if self.use_ws else "HTTP"
        system_detail = f"OpenAI {transport_detail} connected ({model_name})"
        await self.send_json(
            {
                "type": "system",
                "kind": "connection",
                "text": system_detail,
                "timestamp": timezone.now().isoformat(),
            }
        )
        await self._flush_pending_tool_results()

    async def disconnect(self, close_code):
        logger.info(
            "WS DISCONNECT run=%s consumer=%s channel_name=%s close_code=%s tool_result_flow=redis",
            getattr(self, "run_id", None),
            id(self),
            getattr(self, "channel_name", None),
            close_code,
        )
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
        channel_name = getattr(self, "channel_name", None)
        logger.info(
            "WS disconnect close_code=%s run_id=%s channel_name=%s run_group=%s approvals_group=%s",
            close_code,
            self.run_id,
            channel_name,
            self.workspace_group,
            self.approvals_group,
        )
        if channel_layer and channel_name:
            if self.workspace_group:
                await channel_layer.group_discard(self.workspace_group, channel_name)
            if self.approvals_group:
                await channel_layer.group_discard(self.approvals_group, channel_name)

    async def receive_json(self, content, **kwargs):
        message_type = content.get("type")
        if message_type == "chat.message":
            raw_text = (content.get("text") or "").strip()
            if not raw_text:
                return
            await self._accept_user_message(raw_text, persist=True, emit_message=False, mirror_to_transport=True)
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
                    "kind": "connection",
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
                    tool_call_id=tool_call_id,
                    user=self.scope.get("user"),
                    grant_mode=str(content.get("grant_mode") or "once"),
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
        if message_type == "tool_revoke_grant":
            grant_id = content.get("grant_id")
            if grant_id:
                await sync_to_async(revoke_tool_approval_grant)(
                    grant_id=grant_id,
                    user=self.scope.get("user"),
                    run_id=str(self.run_id),
                )
            return
        if message_type == "tool_clear_grants":
            if self.run_id:
                await sync_to_async(clear_tool_approval_grants)(
                    run_id=str(self.run_id),
                    user=self.scope.get("user"),
                )
            return
        if message_type in {"pause_run", "resume_run", "cancel_run"}:
            if not self.run_id:
                return
            handler = {
                "pause_run": pause_run,
                "resume_run": resume_run,
                "cancel_run": cancel_run,
            }[message_type]
            params = {}
            if message_type == "cancel_run":
                params["reason"] = content.get("reason")
            try:
                run_obj = await sync_to_async(handler)(run_id=str(self.run_id), **params)
            except Exception as exc:
                await self.send_json(
                    {
                        "type": "error",
                        "message": str(exc),
                        "timestamp": timezone.now().isoformat(),
                    }
                )
                return
            self.run = run_obj
            await self.send_json(
                {
                    "type": f"{message_type}_ack",
                    "run_id": str(run_obj.id),
                    "status": run_obj.status,
                    "timestamp": timezone.now().isoformat(),
                }
            )
            if message_type == "resume_run" and self._queued_user_message:
                self._queued_user_message = False
                await self._dispatch_to_provider()
            return

    async def _current_run_status(self) -> str | None:
        if not self.run_id:
            return None
        return await _get_run_status(str(self.run_id))

    async def _dispatch_blocked_by_run_status(self) -> bool:
        run_status = await self._current_run_status()
        if run_status in {
            AgentRun.Status.PAUSED,
            AgentRun.Status.WAITING_FOR_USER,
            AgentRun.Status.CANCELED,
            AgentRun.Status.COMPLETED,
            AgentRun.Status.FAILED,
        }:
            logger.info(
                "Dispatch skipped because run is not runnable run=%s status=%s",
                self.run_id,
                run_status,
            )
            return True
        return False

    async def _accept_user_message(
        self,
        raw_text: str,
        *,
        persist: bool,
        emit_message: bool,
        mirror_to_transport: bool = False,
        source_transport: str | None = None,
        author_label: str | None = None,
    ) -> None:
        sanitized_text, secret_types = raw_text, []
        if _should_scrub_prompts():
            sanitized_text, secret_types = _scrub_input_text(raw_text)
            if secret_types:
                secret_list = ", ".join(sorted(set(secret_types)))
                summary_text = (
                    f"Maestro masked {len(secret_types)} secret(s) ({secret_list}) before sending and has your back."
                )
                await self.send_json(
                    {
                        "type": "system",
                        "kind": "security",
                        "text": summary_text,
                        "timestamp": timezone.now().isoformat(),
                    }
                )
                logger.info(
                    "Masked secrets before sending message run=%s secrets=%s",
                    self.run_id,
                    secret_list,
                )
        self.history.append({"role": "user", "content": sanitized_text})
        if persist:
            await _persist_chat_history_event(self.run_id or "", "user", sanitized_text)
        if emit_message:
            await self.send_json(
                {
                    "type": "message",
                    "role": "operator",
                    "direction": "out",
                    "author": author_label or ("You via Telegram" if source_transport else "You"),
                    "text": sanitized_text,
                    "timestamp": timezone.now().isoformat(),
                }
            )
        if mirror_to_transport and not source_transport:
            user = self.scope.get("user")
            await sync_to_async(send_run_transport_message)(
                run_id=str(self.run_id),
                text=sanitized_text,
                author_label=(getattr(user, "get_username", lambda: None)() or "user"),
                control_payload={"event_type": "chat_message", "role": "user"},
            )
        run_status = await _get_run_status(self.run_id or "")
        if run_status in {AgentRun.Status.PAUSED, AgentRun.Status.WAITING_FOR_USER}:
            self._queued_user_message = True
            await self.send_json(
                {
                    "type": "system",
                    "kind": "run_control",
                    "text": "Run is paused. Message queued; resume when you want Maestro to continue.",
                    "timestamp": timezone.now().isoformat(),
                }
            )
            return
        if run_status in {AgentRun.Status.CANCELED, AgentRun.Status.COMPLETED, AgentRun.Status.FAILED}:
            await self.send_json(
                {
                    "type": "system",
                    "kind": "run_control",
                    "text": f"Run is {str(run_status or 'inactive').lower()}. Start a new run to continue.",
                    "timestamp": timezone.now().isoformat(),
                }
            )
            return
        await self._dispatch_to_provider()

    async def _dispatch_to_provider(self):
        self._ensure_system_context()
        async with self._send_lock:
            if self.use_ws:
                await self._dispatch_to_provider_ws()
            else:
                await self._dispatch_to_provider_http()

    async def _dispatch_to_provider_ws(self):
        if await self._dispatch_blocked_by_run_status():
            return
        if not self.session:
            logger.error("Error in consumers._dispatch_to_provider_ws:  No session established")
            return
        tools = self.session_tools if self.session_tools else None
        reconnect_attempts = 0
        max_reconnects = 1
        logger.debug("WS dispatch run=%s tools=%s", self.run_id, bool(tools))
        while True:
            if await self._dispatch_blocked_by_run_status():
                return
            if not self._tool_result_event.is_set():
                logger.info(
                    "WS dispatch waiting for tool output run=%s pending_tool_call_id=%s provider_call_id=%s ts=%s",
                    self.run_id,
                    self._pending_tool_call_id,
                    self._pending_provider_call_id,
                    timezone.now().isoformat(),
                )
                logger.info(
                    "WS dispatch yielding to push handler run=%s pending_tool_call_id=%s provider_call_id=%s",
                    self.run_id,
                    self._pending_tool_call_id,
                    self._pending_provider_call_id,
                )
                return
            input_items = self._build_ws_input_items()
            response_payload = {
                "model": self.session.model if self.session else "unknown",
                "input": input_items,
            }
            logger.debug(
                "WS payload run=%s inputs=%s previous=%s tools=%s",
                self.run_id,
                len(input_items),
                getattr(self.session, "previous_response_id", None),
                len(tools or []),
            )
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
            if self._last_tool_call_id:
                logger.info(
                    "WS response.create after tool call run=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s tool_output_ready=%s ts=%s",
                    self.run_id,
                    self._last_tool_call_id,
                    self._last_provider_call_id,
                    getattr(self.session, "previous_response_id", None),
                    bool(self._tool_output_payload),
                    timezone.now().isoformat(),
                )
                self._last_tool_call_id = None
                self._last_provider_call_id = None
                self._tool_output_payload = None
            await self._log_transport_traffic("WS Send", payload_snapshot)
            try:
                logger.debug(
                    "WS input_items run=%s types=%s",
                    self.run_id,
                    [item.get("type") for item in input_items],
                )
                response = await self.session.create_or_continue(
                    input_items=input_items,
                    tools=tools,
                )
                await self._log_transport_traffic(
                    "WS Rcv",
                    response.get("raw") if isinstance(response, dict) else response,
                )
                logger.debug(
                    "WS response run=%s status=%s tool_calls=%s",
                    self.run_id,
                    response.get("status"),
                    len(response.get("tool_calls") or []),
                )
            except OpenAIResponsesWSPreviousResponseNotFound as exc:
                if self.session:
                    self.session.previous_response_id = None
                await self._handle_ws_failure(
                    exc,
                    input_items,
                    summary=True,
                    attempt=reconnect_attempts + 1,
                    max_attempts=max_reconnects + 1,
                )
                if reconnect_attempts >= max_reconnects:
                    await self._send_error_and_abort(exc)
                    return
                reconnect_attempts += 1
                continue
            except OpenAIResponsesWSException as exc:
                await self._handle_ws_failure(
                    exc,
                    input_items,
                    summary=True,
                    attempt=reconnect_attempts + 1,
                    max_attempts=max_reconnects + 1,
                )
                if reconnect_attempts >= max_reconnects:
                    await self._send_error_and_abort(exc)
                    return
                reconnect_attempts += 1
                continue

            tool_calls = response.get("tool_calls") or []
            if tool_calls:
                for call in tool_calls:
                    logger.info(
                        "WS tool call run=%s name=%s call_id=%s",
                        self.run_id,
                        call.get("name"),
                        call.get("call_id") or call.get("id"),
                    )
                    await self._handle_tool_call(call)
                continue

            logger.debug(
                "WS assistant_text run=%s present=%s",
                self.run_id,
                bool(response.get("text")),
            )
            assistant_text = response.get("text") or ""
            if assistant_text:
                self.history.append({"role": "assistant", "content": assistant_text})
                await _persist_chat_history_event(
                    self.run_id or "",
                    "assistant",
                    assistant_text,
                    model=self.session.model if self.session else None,
                    provider_response_id=response.get("response_id"),
                    step_index=getattr(self.run, "current_step_index", None),
                )
                await self.send_json(
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": assistant_text,
                        "timestamp": timezone.now().isoformat(),
                    }
                )
                await sync_to_async(send_run_transport_message)(
                    run_id=str(self.run_id),
                    text=assistant_text,
                    author_label="assistant",
                    control_payload={"event_type": "assistant_message"},
                )
                return

    async def _dispatch_to_provider_http(self):
        if await self._dispatch_blocked_by_run_status():
            return
        tools_available = self.tool_definitions if self.tool_definitions else None
        model = self.model_name or "unknown"
        while True:
            if await self._dispatch_blocked_by_run_status():
                return
            snapshot_messages = [
                {"role": entry.get("role"), "content": (entry.get("content") or "")[:160]}
                for entry in self.history[-4:]
            ]
            tools_to_send = tools_available if tools_available else None
            payload_snapshot: dict[str, object] = {"model": model, "messages": snapshot_messages}
            if tools_to_send:
                payload_snapshot["tools"] = [entry.get("name") for entry in tools_to_send if entry.get("name")]
            await self._log_transport_traffic("HTTP SEND", payload_snapshot)
            try:
                response = await self.client.complete(
                    self.history,
                    model=model,
                    tools=tools_to_send,
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
                await _persist_chat_history_event(
                    self.run_id or "",
                    "assistant",
                    assistant_text,
                    model=model,
                    provider_response_id=response.get("response_id"),
                    step_index=getattr(self.run, "current_step_index", None),
                )
                await self.send_json(
                    {
                        "type": "message",
                        "role": "assistant",
                        "text": assistant_text,
                        "timestamp": timezone.now().isoformat(),
                    }
                )
                await sync_to_async(send_run_transport_message)(
                    run_id=str(self.run_id),
                    text=assistant_text,
                    author_label="assistant",
                    control_payload={"event_type": "assistant_message"},
                )
                return

    async def _handle_tool_call(self, call: dict[str, object]):
        tool_name = str(call.get("name") or "").strip()

        logger.info(
            "AgentChatConsumer._handle_tool_call start run=%s tool=%s",
            self.run_id,
            tool_name,
        )

        if not tool_name:
            await self.send_json(
                {
                    "type": "tool_error",
                    "message": "Tool call missing name",
                }
            )
            logger.error("AgentChatConsumer._handle_tool_call missing tool name call=%s", call)
            return

        raw_call_id = call.get("call_id") or call.get("id")
        call_id = str(raw_call_id).strip() if raw_call_id is not None else ""
        if not call_id:
            raise ValueError("No call_id present in _handle_tool_call")
        args = self._parse_tool_args(call.get("arguments"))

        #if tool_name == "repo_tree":
        #    args = _normalize_repo_tree_args(args)
        #    logger.info(f"Repo tree args are {args}")

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

        try:
            tool_call = await sync_to_async(request_tool_call_approval)(
                run_id=str(self.run_id),
                tool_name=tool_name,
                args=args,
                requires_approval=requires_approval,
            )
        except ToolCommandGuardrailError as exc:
            denied_id = await _record_denied_tool_call(
                run_id=str(self.run_id),
                tool_name=tool_name,
                args=args,
                provider_call_id=call_id,
                reason=str(exc),
            )
            await self._stage_provider_tool_feedback(
                tool_call_id=denied_id,
                provider_call_id=call_id,
                payload={
                    "ok": False,
                    "error": {
                        "code": "tool_runner.TOOL_ALIAS_REJECTED",
                        "message": str(exc),
                        "details": {
                            "requested_tool": tool_name,
                            "recommended_tool": exc.recommended_tool,
                            "reason": exc.reason,
                            "provider_call_id": call_id,
                        },
                    },
                },
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
        except Exception:
            logger.exception(
                "AgentChatConsumer._handle_tool_call request_tool_call_approval failed "
                "run=%s tool=%s provider_call_id=%s",
                getattr(self, "run_id", None),
                tool_name,
                call_id,
            )
            await self.send_json(
                {
                    "type": "tool_error",
                    "tool_name": tool_name,
                    "message": "Failed to create tool call request.",
                }
            )
            return

        tool_call_id = str(tool_call.id)

        try:
            await _update_provider_metadata(tool_call_id, call_id)
            logger.info(
                "_handle_tool_call persisted provider call id run=%s tool_call_id=%s provider_call_id=%s",
                getattr(self, "run_id", None),
                tool_call_id,
                call_id,
            )
            logger.debug("completed await _update_provider_metadata(tool_call_id, call_id)")
            tool_call.provider_call_id = call_id
        except Exception:
            logger.exception(
                "AgentChatConsumer._handle_tool_call failed to update provider metadata "
                "run=%s tool_call_id=%s provider_call_id=%s",
                getattr(self, "run_id", None),
                tool_call_id,
                call_id,
            )

        await self.send_json(
            {
                "type": "tool_request",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "requires_approval": tool_call.requires_approval,
                "awaiting_approval": tool_call.status == ToolCall.Status.PENDING_APPROVAL,
                "risk": entry.risk,
                "args": args,
                "status": tool_call.status,
                "approval_options": (
                    grant_options_for_tool_call(tool_call)
                    if tool_call.status == ToolCall.Status.PENDING_APPROVAL
                    else []
                ),
                "approval_metadata": tool_call.approval_metadata or {},
            }
        )

        ts = timezone.now().isoformat()
        logger.info(
            "tool_request push ts=%s run=%s tool_call_id=%s tool=%s requires_approval=%s status=%s",
            ts,
            getattr(self, "run_id", None),
            tool_call_id,
            tool_name,
            requires_approval,
            tool_call.status,
        )

        logger.info(
            "TOOL REQUEST EMITTED run=%s tool_call_id=%s tool=%s provider_call_id=%s",
            getattr(self, "run_id", None),
            tool_call_id,
            tool_name,
            call_id,
        )
        self._pending_tool_call_id = tool_call_id
        self._pending_provider_call_id = call_id
        self._awaiting_tool_output = True
        self._tool_result_event.clear()
        return

    def _tool_names(self) -> list[object | None]:
        return [
            entry.get("name")
            for entry in self.tool_definitions
            if isinstance(entry.get("name"), str) and entry.get("name")
        ]

    def _ensure_system_context(self) -> None:
        agent = self.agent
        if not agent:
            return
        context = build_system_context(
            agent,
            model_name=self.model_name or agent.default_model,
            transport=self.transport,
            tool_names=self._tool_names(),
        )
        self.system_context = context
        if not context:
            return
        for entry in self.history:
            if entry.get("role") == "system":
                entry["content"] = context
                return
        self.history.insert(0, {"role": "system", "content": context})

    async def _flush_pending_tool_results(self, limit: int = 20) -> None:
        if not self.run_id:
            return
        ts = timezone.now().isoformat()
        logger.info(
            "flushing pending tool results run=%s limit=%s ts=%s",
            self.run_id,
            limit,
            ts,
        )
        try:
            pending = await sync_to_async(pop_pending_tool_results)(self.run_id, limit)
        except Exception as exc:
            logger.exception(
                "Failed to pop pending tool results run=%s limit=%s",
                self.run_id,
                limit,
                exc_info=exc,
            )
            return
        if not pending:
            logger.info(
                "no pending tool results for run=%s ts=%s",
                self.run_id,
                timezone.now().isoformat(),
            )
            if self._awaiting_tool_output and not self._tool_result_event.is_set():
                self._tool_result_event.set()
                self._awaiting_tool_output = False
            return
        tool_call_ids = [entry.get("tool_call_id") for entry in pending]
        logger.info(
            "delivering %d pending tool results run=%s tool_call_ids=%s ts=%s",
            len(pending),
            self.run_id,
            tool_call_ids,
            timezone.now().isoformat(),
        )
        last_payload: dict[str, object] | None = None
        for payload in pending:
            tool_call_id = payload.get("tool_call_id")
            logger.debug(
                "delivering tool_result run=%s tool_call_id=%s keys=%s ts=%s",
                self.run_id,
                tool_call_id,
                list(payload.keys()),
                timezone.now().isoformat(),
            )
            await self.send_json({"type": "tool_result", **payload})
            result_data = payload.get("result") or payload.get("stdout") or payload.get("stderr") or payload
            try:
                tool_output_text = json.dumps(result_data, ensure_ascii=False)
            except (TypeError, ValueError):
                tool_output_text = str(result_data)
            provider_call_id = (
                payload.get("provider_call_id")
                or self._pending_provider_call_id
                or ""
            )
            provider_call_id = str(provider_call_id).strip() or None
            previous_response_id = getattr(self.session, "previous_response_id", None)
            should_stage_for_provider = bool(provider_call_id and previous_response_id)
            if should_stage_for_provider:
                self.history.append(
                    {
                        "role": "tool",
                        "content": tool_output_text,
                        "tool_call_id": tool_call_id,
                        "provider_call_id": provider_call_id,
                    }
                )
            else:
                logger.warning(
                    "Skipping tool_output history injection run=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s",
                    self.run_id,
                    tool_call_id,
                    provider_call_id,
                    previous_response_id,
                )
            logger.info(
                "tool_output processed run=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s staged_for_provider=%s ts=%s output_len=%s",
                self.run_id,
                tool_call_id,
                provider_call_id,
                previous_response_id,
                should_stage_for_provider,
                timezone.now().isoformat(),
                len(tool_output_text),
            )
            if should_stage_for_provider:
                last_payload = payload
        if last_payload:
            self._last_tool_call_id = last_payload.get("tool_call_id")
            api_provider_call_id = last_payload.get("provider_call_id") or self._pending_provider_call_id
            self._last_provider_call_id = api_provider_call_id
            self._tool_output_payload = last_payload
            self._pending_tool_call_id = None
            self._pending_provider_call_id = None
            self._awaiting_tool_output = False
            if not self._tool_result_event.is_set():
                self._tool_result_event.set()
        elif self._awaiting_tool_output:
            logger.warning(
                "No provider-eligible tool outputs available after flush run=%s pending_tool_call_id=%s pending_provider_call_id=%s previous_response_id=%s",
                self.run_id,
                self._pending_tool_call_id,
                self._pending_provider_call_id,
                getattr(self.session, "previous_response_id", None),
            )
            self._pending_tool_call_id = None
            self._pending_provider_call_id = None
            self._awaiting_tool_output = False
            if not self._tool_result_event.is_set():
                self._tool_result_event.set()

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

    async def _stage_provider_tool_feedback(
        self,
        *,
        tool_call_id: str,
        provider_call_id: str | None,
        payload: dict[str, object],
    ) -> None:
        provider_call_id = str(provider_call_id or "").strip() or None
        if not provider_call_id:
            logger.warning(
                "Skipping provider tool feedback staging run=%s tool_call_id=%s because provider_call_id is missing",
                self.run_id,
                tool_call_id,
            )
            return
        try:
            tool_output_text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            tool_output_text = str(payload)
        self.history.append(
            {
                "role": "tool",
                "content": tool_output_text,
                "tool_call_id": tool_call_id,
                "provider_call_id": provider_call_id,
            }
        )
        self._last_tool_call_id = tool_call_id
        self._last_provider_call_id = provider_call_id
        self._tool_output_payload = {
            "tool_call_id": tool_call_id,
            "provider_call_id": provider_call_id,
            "result": payload,
        }
        self._pending_tool_call_id = None
        self._pending_provider_call_id = None
        self._awaiting_tool_output = False
        if not self._tool_result_event.is_set():
            self._tool_result_event.set()
        logger.info(
            "Staged provider tool feedback run=%s tool_call_id=%s provider_call_id=%s payload_keys=%s",
            self.run_id,
            tool_call_id,
            provider_call_id,
            list(payload.keys()),
        )

    async def push(self, event: dict):
        """
        Handle channel-layer events sent to the run.<uuid> group.
        runs.services.events.broadcast_run_event uses {"type": "push", "payload": {...}}.

        Ownership rules:
        - push() forwards lightweight events and flushes pending Redis-backed tool results.
        - _handle_tool_call() emits the initial browser-facing tool_request for incoming calls.
        """

        logger.info("***************   AgentChatConsumer.push started   **************************")
        logger.info(
            "push raw event run=%s consumer=%s raw_event=%s",
            getattr(self, "run_id", None),
            id(self),
            event,
        )

        raw_event = event or {}
        payload = raw_event.get("payload")
        parsed_event = payload if isinstance(payload, dict) else raw_event
        if payload and not isinstance(payload, dict) and payload is not None:
            logger.warning(
                "AgentChatConsumer.push received non-dict payload run=%s payload=%r",
                getattr(self, "run_id", None),
                payload,
            )

        event_type = parsed_event.get("event")
        data = parsed_event.get("data") or {}
        run_id_from_event = parsed_event.get("run_id") or raw_event.get("run_id") or data.get("run_id")
        tool_call_id = data.get("tool_call_id") or data.get("call_id")
        provider_call_id = data.get("provider_call_id")

        if event_type is None and "tool_result_ready" in (str(parsed_event) + str(raw_event)):
            logger.warning(
                "push parsed event_type missing but payload contains tool_result_ready run=%s event=%s payload=%s",
                getattr(self, "run_id", None),
                event_type,
                parsed_event,
            )

        logger.info(
            "parsed push run=%s event_type=%s run_id=%s tool_call_id=%s provider_call_id=%s data_keys=%s",
            getattr(self, "run_id", None),
            event_type,
            run_id_from_event,
            tool_call_id,
            provider_call_id,
            sorted(data.keys()),
        )

        if event_type == "tool_result_ready":
            logger.info(
                "Event type -> tool_result_ready AgentChatConsumer.push tooling run=%s detected pending tool results",
                getattr(self, "run_id", None),
            )
            await self._flush_pending_tool_results()
            if self._tool_output_payload:
                logger.info(
                    "tool_result_ready resuming provider dispatch run=%s tool_call_id=%s provider_call_id=%s",
                    getattr(self, "run_id", None),
                    self._last_tool_call_id,
                    self._last_provider_call_id,
                )
                await self._dispatch_to_provider()
            return

        if event_type == DEBUG_GROUP_ECHO_EVENT:
            logger.info(
                "4:  Event type -> DEBUG_GROUP_ECHO_EVENT AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "debug_group_echo", **data})
            return

        if event_type == DEBUG_GROUP_ECHO_FROM_EVENTS:
            logger.info(
                "4:  Event type -> DEBUG_GROUP_ECHO_FROM_EVENT AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "debug_group_echo_from_events", **data})
            return

        if event_type == TOOL_CALL_STATUS_EVENT:
            logger.info(
                "4:  Event type -> TOOL_CALL_STATUS_EVENT AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "tool_status", **data})
            return

        if event_type == TOOL_APPROVAL_GRANTS_UPDATED_EVENT:
            await self.send_json(
                {
                    "type": "approval_grants",
                    "run_id": data.get("run_id"),
                    "grants": data.get("grants") or [],
                }
            )
            return

        if event_type == "chat_message" and data.get("source_transport"):
            await self._accept_user_message(
                str(data.get("text") or ""),
                persist=False,
                emit_message=True,
                mirror_to_transport=False,
                source_transport=str(data.get("source_transport") or ""),
                author_label=str(data.get("author_label") or "you").strip().lower(),
            )
            return

        if event_type == "assistant_message":
            logger.info(
                "4:  Event type -> assistant_message AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json(
                {
                    "type": "message",
                    "role": "assistant",
                    "text": data.get("content", ""),
                    "model": data.get("model"),
                    "timestamp": timezone.now().isoformat(),
                }
            )
            return

        if event_type == TOOL_CALL_DENIED_EVENT:
            logger.info(
                "4:  Event type -> TOOL_CALL_DENIED_EVENT AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "tool_denied", **data})
            return

        if event_type == "tool_call_completed":
            logger.info(
                "4:  Event type -> tool_call_completed AgentChatConsumer.push debug echo run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "tool_call_completed", **data})
            await self.send_json({"type": "tool_result", **data})
            return

        # Final catch-all for non-tool events only.
        logger.info(
            "4:  Event type -> CATCH_ALL at end AgentChatConsumer.push debug echo run=%s data=%s",
            getattr(self, "run_id", None),
            data,
        )
        await self.send_json(
            {
                "type": event_type or "event",
                "data": data,
                "event": event_type,
            }
        )

    def _build_input_items(self):
        previous_response_id = getattr(self.session, "previous_response_id", None)
        outstanding_provider_call_id = None
        if self._tool_output_payload:
            raw_provider_id = self._tool_output_payload.get("provider_call_id")
            outstanding_provider_call_id = (
                str(raw_provider_id).strip() or None
                if raw_provider_id is not None
                else None
            )
        return build_input_items(
            self.history,
            previous_response_id=previous_response_id,
            outstanding_provider_call_id=outstanding_provider_call_id,
            run_id=self.run_id,
        )

    def _build_ws_input_items(self) -> list[dict[str, object]]:
        previous_id = getattr(self.session, "previous_response_id", None)
        if not previous_id:
            return self._build_input_items()

        # When continuing a Responses WS turn after a tool call, send only the
        # staged tool outputs for the outstanding provider call. Re-sending the
        # full system/user history together with previous_response_id duplicates
        # the initial context on the continuation request.
        if self.history and self.history[-1].get("role") == "tool":
            tool_entries: list[dict[str, object]] = []
            for entry in reversed(self.history):
                if entry.get("role") != "tool":
                    break
                tool_entries.append(entry)
            tool_entries.reverse()
            return build_input_items(
                tool_entries,
                previous_response_id=previous_id,
                outstanding_provider_call_id=(
                    str(self._tool_output_payload.get("provider_call_id") or "").strip()
                    if self._tool_output_payload
                    else None
                ),
                run_id=self.run_id,
            )

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
            attempt: int = 1,
            max_attempts: int = 1,
    ) -> None:
        summary_text = self._summarize_input_items(input_items) if summary else ""
        logger.warning(
            "OpenAI WS reconnect triggered run=%s agent=%s reason=%s summary=%s",
            self.run_id,
            self.agent.slug if self.agent else "unknown",
            exc,
            summary_text,
        )
        classification = getattr(exc, "classification", "unknown")
        request_id = getattr(exc, "request_id", None) or "unknown"
        param_path = getattr(exc, "param", "") or ""
        if classification == "validation_error":
            message = f"Payload invalid (do not retry) request_id={request_id}"
            if param_path:
                message += f" path={param_path}"
            kind = "error"
        elif classification == "ratelimit":
            message = (
                f"Rate limited, retrying (attempt {attempt}/{max_attempts}) request_id={request_id}"
            )
            kind = "connection"
        elif classification == "prev_not_found":
            message = (
                f"Previous response not found; resetting session continuity request_id={request_id}"
            )
            kind = "connection"
        else:
            message = f"OpenAI WS connection hiccup: {exc}. Request_id={request_id}"
            kind = "connection"
        await self.send_json(
            {
                "type": "system",
                "kind": kind,
                "text": message,
                "timestamp": timezone.now().isoformat(),
            }
        )
        if summary_text:
            await self.send_json(
                {
                    "type": "system",
                    "kind": "connection",
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
