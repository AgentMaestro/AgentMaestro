import asyncio
import json
import re
import time
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from comms.services.agent_chat_bridge import send_run_transport_message
from core.models import WorkspaceMembership
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from logging_utils import get_app_logger
from llm.models import LLMModelProfile
from llm.services.model_failover import is_retryable_model_failure
from llm.services.providers.retry import retry_with_backoff
from llm.services.registry import get_client
from llm.services.tool_code import extract_code_like_tool_calls
from llm.system_context import build_system_context
from memory.remember_requests import capture_explicit_user_memory_request
from runs.models import AgentRun, AgentStep, Artifact, RunEvent
from runs.services.event_builders import (
    build_assistant_message_payload,
    build_chat_message_payload,
)
from runs.services.events import append_event
from runs.services.artifacts import pending_artifacts, serialize_artifact
from runs.services.handoff import build_handoff_system_note, get_run_handoff_payload
from runs.services.input_items import build_input_items, build_ws_request_input_items
from runs.services.memory import get_or_create_run_memory
from runs.services.memory_bootstrap import bootstrap_memory_for_first_turn
from runs.services.recovery import cancel_run, pause_run, resume_run
from runs.services.steps import append_step
from runs.services.tool_output import compact_tool_output_text
from logging_utils import scrub_sensitive_text_with_types, scrub_sensitive_value
from tools.models import ToolCall
from tools.policy import ToolNotAllowedError, assert_tool_allowed, get_effective_tools
from tools.services.approval_grants import active_grants_for_run
from tools.services.approvals import (
    TOOL_APPROVAL_GRANTS_UPDATED_EVENT,
    TOOL_CALL_DENIED_EVENT,
    TOOL_CALL_STATUS_EVENT,
    approve_tool_call,
    clear_tool_approval_grants,
    deny_tool_call,
    grant_options_for_tool_call,
    request_tool_call_approval,
    revoke_tool_approval_grant,
)
from tools.services.command_guardrails import ToolCommandGuardrailError
from tools.services.result_bus import pop_pending_tool_results

from agents.models import Agent
from agents.utils import (
    build_transport_status,
    format_provider_display,
    get_agent_telegram_mirror_enabled,
    normalize_provider_for_model,
)

logger = get_app_logger(__name__)
DEBUG_GROUP_ECHO_EVENT = "debug_group_echo"
DEBUG_GROUP_ECHO_FROM_EVENTS = "debug_group_echo_from_events"
AGENTS_MD_BOOTSTRAP_EVENT = "agents_md_bootstrap"


def _should_scrub_prompts() -> bool:
    should_scrub = getattr(settings, "SCRUB_PROMPTS", True)
    if getattr(settings, "TESTING", False) and not getattr(
        settings, "SCRUB_PROMPTS_FOR_TESTS", False
    ):
        return False
    return should_scrub


def _show_condensed_system_logs() -> bool:
    return getattr(settings, "SHOW_CONDENSED_SYSTEM_LOGS", True)


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
    return LLMModelProfile.objects.filter(name=policy_name, is_active=True).order_by("name").first()


@database_sync_to_async
def _get_run_step_admin_link(
    run_id: str, step_index: int | None = None
) -> dict[str, object] | None:
    steps = AgentStep.objects.filter(run_id=run_id)
    if step_index is not None:
        step = steps.filter(step_index=step_index).order_by("-created_at").first()
    else:
        step = steps.order_by("-step_index", "-created_at").first()
    if not step:
        return None
    try:
        admin_step_url = reverse("admin:runs_agentstep_change", args=[str(step.id)])
    except NoReverseMatch:
        logger.warning(
            "AgentStep admin reverse missing; skipping condensed log link run=%s step_id=%s",
            run_id,
            step.id,
        )
        return None
    return {
        "admin_step_url": admin_step_url,
        "admin_step_label": f"step {step.step_index}",
        "admin_step_index": step.step_index,
    }


def _scrub_input_text(text: str | None) -> tuple[str, list[str]]:
    return scrub_sensitive_text_with_types(text)


@database_sync_to_async
def _record_denied_tool_call(
    *,
    run_id: str,
    tool_name: str,
    args: dict[str, object],
    provider_call_id: str,
    reason: str,
) -> str:
    run = AgentRun.objects.select_related("workspace").get(id=run_id)
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
def _get_tool_call_details(tool_call_id: str) -> dict[str, object] | None:
    return ToolCall.objects.filter(id=tool_call_id).values("tool_name", "args", "status").first()


@database_sync_to_async
def _get_outstanding_provider_call_ids(run_id: str) -> list[str]:
    if not run_id:
        return []
    outstanding_statuses = [
        ToolCall.Status.REQUESTED,
        ToolCall.Status.PENDING_APPROVAL,
        ToolCall.Status.QUEUED,
        ToolCall.Status.RUNNING,
    ]
    return list(
        ToolCall.objects.filter(run_id=run_id, status__in=outstanding_statuses)
        .exclude(provider_call_id="")
        .order_by("created_at")
        .values_list("provider_call_id", flat=True)
    )


@database_sync_to_async
def _get_replayable_tool_result_tail(run_id: str) -> tuple[list[dict[str, object]], bool]:
    if not run_id:
        return [], False

    terminal_statuses = {
        ToolCall.Status.COMPLETED,
        ToolCall.Status.FAILED,
        ToolCall.Status.DENIED,
    }
    tool_calls = list(
        ToolCall.objects.filter(run_id=run_id)
        .exclude(provider_call_id="")
        .order_by("-created_at", "-id")
        .values(
            "id",
            "tool_name",
            "status",
            "exit_code",
            "stdout",
            "stderr",
            "result",
            "error",
            "run_id",
            "correlation_id",
            "provider_call_id",
            "created_at",
            "started_at",
            "ended_at",
        )
    )
    if not tool_calls:
        return [], False

    replayable: list[dict[str, object]] = []
    for row in tool_calls:
        status = str(row.get("status") or "").strip().upper()
        if status not in terminal_statuses:
            return [], True

        result_payload = row.get("result") if isinstance(row.get("result"), dict) else {}
        if not result_payload:
            error_text = str(row.get("error") or "").strip()
            if error_text:
                result_payload = {"error": error_text}
            elif status == ToolCall.Status.DENIED:
                result_payload = {"error": "Tool call denied."}

        if (
            not result_payload
            and not str(row.get("stdout") or "").strip()
            and not str(row.get("stderr") or "").strip()
        ):
            return [], True

        payload: dict[str, object] = {
            "tool_call_id": str(row.get("id") or "").strip(),
            "status": str(row.get("status") or "").strip(),
            "tool_name": str(row.get("tool_name") or "").strip(),
            "stdout": str(row.get("stdout") or ""),
            "stderr": str(row.get("stderr") or ""),
            "result": result_payload,
            "run_id": str(row.get("run_id") or "").strip(),
            "correlation_id": str(row.get("correlation_id") or "").strip(),
            "provider_call_id": str(row.get("provider_call_id") or "").strip() or None,
        }

        started_at = row.get("started_at")
        ended_at = row.get("ended_at")
        if started_at and ended_at:
            try:
                duration_ms = int((ended_at - started_at).total_seconds() * 1000)
            except Exception:
                duration_ms = None
            if duration_ms is not None:
                payload["duration_ms"] = duration_ms

        replayable.append(payload)

    replayable.reverse()
    return replayable, False


@database_sync_to_async
def _assert_tool_allowed(agent: Agent, user, tool_name: str):
    return assert_tool_allowed(agent, user, tool_name)


@database_sync_to_async
def _create_agent_run(agent: Agent, user, started_at: timezone.datetime):
    run = AgentRun.objects.create(
        workspace=agent.workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        started_at=started_at,
        input_text="",
    )
    get_or_create_run_memory(run)
    return run


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
def _set_run_previous_response_id(run_id: str, previous_response_id: str | None) -> None:
    normalized = str(previous_response_id or "").strip() or ""
    AgentRun.objects.filter(id=run_id).update(previous_response_id=normalized)


@database_sync_to_async
def _set_run_agents_md_bootstrap_complete(run_id: str, complete: bool = True) -> None:
    AgentRun.objects.filter(id=run_id).update(agents_md_bootstrap_complete=bool(complete))


@database_sync_to_async
def _get_run_handoff(run_id: str) -> dict[str, object] | None:
    if not run_id:
        return None
    return get_run_handoff_payload(run_id)


@database_sync_to_async
def _build_transport_status(agent: Agent):
    return build_transport_status(agent)


OPENAI_RESPONSE_ID_PATTERN = re.compile(r"^resp_[A-Za-z0-9_-]+$")


def _normalize_openai_response_id(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not OPENAI_RESPONSE_ID_PATTERN.fullmatch(text):
        logger.warning("Discarding invalid OpenAI response id value=%r", text)
        return None
    return text


def _normalize_provider_response_id(provider: object, value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if str(provider or "").strip().lower() == "openai":
        return _normalize_openai_response_id(text)
    return text


@database_sync_to_async
def _get_effective_tools(agent: Agent, user):
    return get_effective_tools(agent, user)


@database_sync_to_async
def _get_active_tool_approval_grants(run_id: str):
    return active_grants_for_run(run_id)


@database_sync_to_async
def _get_model_failover_candidates(agent: Agent, provider: str, model_name: str):
    return agent.get_model_failover_candidates(primary_provider=provider, primary_model=model_name)


@database_sync_to_async
def _get_backup_retry_policy(agent: Agent):
    return agent.get_backup_retry_policy()


@database_sync_to_async
def _get_last_assistant_model_for_run(run_id: str) -> str | None:
    payload = (
        RunEvent.objects.filter(run_id=run_id, event_type="assistant_message")
        .order_by("-seq")
        .values_list("payload", flat=True)
        .first()
    )
    if not isinstance(payload, dict):
        return None
    model_name = str(payload.get("model") or "").strip()
    return model_name or None


@database_sync_to_async
def _get_artifact_context_events(run_id: str) -> list[dict[str, object]]:
    if not run_id:
        return []
    return list(
        RunEvent.objects.filter(
            run_id=run_id,
            event_type__in=["artifact_context", "artifact_removed", "artifact_consumed"],
        )
        .order_by("seq")
        .values("event_type", "payload", "seq")
    )


@database_sync_to_async
def _consume_artifact_contexts(run_id: str, artifact_ids: list[str]) -> dict[str, object] | None:
    normalized_ids = [str(artifact_id or "").strip() for artifact_id in artifact_ids if str(artifact_id or "").strip()]
    if not run_id or not normalized_ids:
        return None
    consumed_at = timezone.now().isoformat()
    consumed_artifacts = [
        serialize_artifact(artifact)
        for artifact in Artifact.objects.filter(run_id=run_id, id__in=normalized_ids).order_by("created_at")
    ]
    marked_count = 0
    for artifact in Artifact.objects.filter(run_id=run_id, id__in=normalized_ids):
        metadata = dict(artifact.metadata or {})
        if str(metadata.get("consumed_at") or "").strip():
            continue
        metadata["consumed_at"] = consumed_at
        artifact.metadata = metadata
        artifact.save(update_fields=["metadata", "updated_at"])
        marked_count += 1
    remaining = pending_artifacts(Artifact.objects.filter(run_id=run_id).order_by("created_at"))[:20]
    remaining_payload = [serialize_artifact(artifact) for artifact in remaining]
    append_event(
        run_id=run_id,
        event_type="artifact_consumed",
        payload={
            "artifact_ids": normalized_ids,
            "artifact_count": len(normalized_ids),
            "consumed_artifacts": consumed_artifacts,
            "artifacts": remaining_payload,
            "timestamp": consumed_at,
        },
        broadcast_to_run=True,
    )
    return {
        "artifact_ids": normalized_ids,
        "artifact_count": len(normalized_ids),
        "consumed_artifacts": consumed_artifacts,
        "remaining_artifacts": remaining_payload,
        "consumed_at": consumed_at,
        "marked_count": marked_count,
    }


@database_sync_to_async
def _bootstrap_memory_for_first_turn(run_id: str, agent_id: str, user_text: str):
    if not run_id or not agent_id:
        return None
    run = AgentRun.objects.select_related("workspace", "agent").get(id=run_id)
    agent = Agent.objects.select_related("workspace").get(id=agent_id)
    return bootstrap_memory_for_first_turn(run, agent, user_text)


@database_sync_to_async
def _capture_explicit_user_memory(run_id: str, user_id: int | None, user_text: str):
    if not run_id or not user_id or not user_text:
        return None
    return capture_explicit_user_memory_request(
        user=user_id,
        text=user_text,
        source_ref=f"chat:{run_id}",
    )


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


def _payload_mentions_agents_md(value: object) -> bool:
    if isinstance(value, dict):
        path_value = value.get("path")
        if str(path_value or "").strip() and Path(str(path_value)).name.upper() == "AGENTS.MD":
            return True
        for nested_key in ("args", "result", "content", "stdout", "stderr", "payload"):
            if _payload_mentions_agents_md(value.get(nested_key)):
                return True
        return False
    if isinstance(value, list):
        return any(_payload_mentions_agents_md(item) for item in value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        if "AGENTS.md" in text or "AGENTS.MD" in text:
            return True
        try:
            return _payload_mentions_agents_md(json.loads(text))
        except Exception:
            return False
    return False


@database_sync_to_async
def _run_has_agents_md_bootstrap_complete(run_id: str) -> bool:
    if not run_id:
        return False
    cached = (
        AgentRun.objects.filter(id=run_id)
        .values_list("agents_md_bootstrap_complete", flat=True)
        .first()
    )
    if cached:
        return True
    events = RunEvent.objects.filter(
        run_id=run_id,
        event_type__in=["tool_call_completed", AGENTS_MD_BOOTSTRAP_EVENT],
    ).order_by("seq")
    for event in events:
        if event.event_type == AGENTS_MD_BOOTSTRAP_EVENT:
            return True
        payload = event.payload if isinstance(event.payload, dict) else {}
        tool_name = str(payload.get("tool_name") or "").strip().lower()
        if tool_name != "file_read":
            continue
        if _payload_mentions_agents_md(payload):
            return True
    return False


def _normalize_repo_tree_args(args: dict[str, object]) -> dict[str, object]:
    if args is None:
        return {}

    normalized = dict(args)
    logger.info("Repo_tree args are:  %s", normalized)

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
        logger.info(f"repo_tree path is {normalized['path']}")

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
    async def send_json(self, content, close=False):
        await super().send_json(scrub_sensitive_value(content), close=close)

    agent: Agent | None = None
    session = None
    client = None
    run_id: str | None = None
    model_name: str = ""
    transport: str = "http"
    use_ws: bool = False
    provider: str = ""
    provider_label: str = "Provider"
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
        self._pending_provider_call_ids: set[str] = set()
        self._last_tool_call_id: str | None = None
        self._last_provider_call_id: str | None = None
        self._tool_output_payload: dict[str, object] | None = None
        self._tool_output_payloads: list[dict[str, object]] = []
        self._tool_output_provider_call_ids: list[str] = []
        self._awaiting_tool_output = False
        self._agents_md_bootstrap_complete = False
        self._response_chain_previous_id: str = ""
        self._artifact_context_ids: set[str] = set()
        self._consumed_artifact_context_ids: set[str] = set()
        self._system_context_marker = "_agentmaestro_system_context"
        self._model_candidates: list[dict[str, object]] = []
        self._active_model_candidate_index: int = 0
        self._backup_retry_policy: dict[str, object] = {}

    async def _log_transport_traffic(self, label: str, data: object | None):
        if not data:
            return
        condensed = _show_condensed_system_logs()
        try:
            payload_text = (
                json.dumps(
                    self._condense_transport_payload(label, data), ensure_ascii=False, default=str
                )
                if condensed
                else json.dumps(data, ensure_ascii=False, default=str)
            )
        except Exception:
            payload_text = str(data)
        provider_label = self.provider_label or "Provider"
        model_label = self.model_name or "unknown"
        transport_label = self._transport_label()
        provider_marker = f"[{provider_label}:{model_label}:{transport_label}]"
        message = {
            "type": "system",
            "text": f"{provider_marker} [{str(label)}] {payload_text}",
            "timestamp": timezone.now().isoformat(),
            "provider": self.provider,
            "provider_label": provider_label,
            "model": model_label,
            "transport": self.transport,
            "transport_label": transport_label,
            "log_mode": "condensed" if condensed else "full",
        }
        if condensed and self.run_id:
            step_link = await _get_run_step_admin_link(
                self.run_id,
                getattr(self.run, "current_step_index", None),
            )
            if step_link:
                message.update(step_link)
        await self.send_json(message)

    def _condense_transport_payload(self, label: str, data: object) -> object:
        if not isinstance(data, dict):
            return data
        summary: dict[str, object] = {}
        text_label = str(label).upper()
        if "model" in data and data.get("model"):
            summary["model"] = data.get("model")
        if "HTTP SEND" in text_label or "WS SEND" in text_label:
            if "type" in data:
                summary["type"] = data.get("type")
            if "store" in data:
                summary["store"] = data.get("store")
            previous_response_id = str(data.get("previous_response_id") or "").strip()
            if previous_response_id:
                summary["previous_response_id"] = previous_response_id
            if isinstance(data.get("messages"), list):
                summary["messages"] = len(data.get("messages") or [])
            if isinstance(data.get("input"), list):
                summary["input_items"] = len(data.get("input") or [])
            if isinstance(data.get("tools"), list):
                summary["tools"] = self._tool_names_from_payload(data.get("tools") or [])
            return summary or data
        response_payload = data.get("response") if "response" in data else data
        summary["response"] = self._summarize_provider_response(response_payload)
        return summary

    @staticmethod
    def _tool_names_from_payload(tools: list[object]) -> list[str]:
        names: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name") or tool.get("function", {}).get("name") or "").strip()
            if tool_name:
                names.append(tool_name)
        return names

    @staticmethod
    def _summarize_provider_response(response: object) -> object:
        if not isinstance(response, dict):
            return response
        raw_response = response.get("response")
        if not isinstance(raw_response, dict):
            raw_response = response
        summary: dict[str, object] = {}
        response_id = str(
            response.get("response_id")
            or raw_response.get("response_id")
            or raw_response.get("id")
            or ""
        ).strip()
        if response_id:
            summary["response_id"] = response_id
        text_value = response.get("text")
        if isinstance(text_value, str):
            summary["text_len"] = len(text_value)
        if "text_len" not in summary:
            raw_text = raw_response.get("text") or raw_response.get("output_text") or ""
            if isinstance(raw_text, str):
                summary["text_len"] = len(raw_text)
        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list):
            summary["tool_calls"] = len(tool_calls)
        else:
            raw_tool_calls = raw_response.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                summary["tool_calls"] = len(raw_tool_calls)
        return summary

    def _transport_label(self) -> str:
        return "WS" if self.use_ws else "HTTP"

    def _transport_name(self) -> str:
        return "WebSocket" if self.use_ws else "HTTP"

    def _runtime_transport_label(self) -> str:
        provider_label = (self.provider_label or "Provider").strip() or "Provider"
        model_label = self.model_name or "unknown"
        return f"{provider_label}:{model_label}:{self._transport_label()}"

    async def _switch_model_candidate(self, candidate_index: int) -> bool:
        if not self._model_candidates:
            return False
        if candidate_index < 0 or candidate_index >= len(self._model_candidates):
            return False
        candidate = self._model_candidates[candidate_index]
        previous_client = self.client
        previous_model = self.model_name
        previous_use_ws = self.use_ws
        preserved_previous_response_id = self._current_previous_response_id()
        if previous_use_ws and self.run_id:
            closer = getattr(previous_client, "close_ws_session", None)
            if callable(closer):
                try:
                    await closer(str(self.run_id), model=previous_model or None)
                except Exception:
                    logger.exception(
                        "Failed to close ws session before switching model candidate run=%s provider=%s model=%s",
                        self.run_id,
                        self.provider,
                        previous_model,
                    )
        self._active_model_candidate_index = candidate_index
        self.provider = (
            str(candidate.get("provider") or self.provider or "openai").strip() or "openai"
        )
        self.model_name = str(candidate.get("model") or self.model_name or "").strip()
        self.provider_label = format_provider_display(self.provider)
        self.client = get_client(self.provider)
        transport_resolver = getattr(self.client, "resolve_transport", None)
        resolved_transport = (
            transport_resolver()
            if callable(transport_resolver)
            else getattr(self.client, "transport", "http")
        )
        self.transport = (resolved_transport or "http").lower()
        self.use_ws = self.transport == "ws"
        self.current_runtime_label = self._runtime_transport_label()
        if self.use_ws:
            self.session = await self.client.get_ws_session(
                self.run_id,
                self.model_name,
                agent_id=str(self.agent.id) if self.agent else None,
            )
            if preserved_previous_response_id and self.session:
                self.session.previous_response_id = preserved_previous_response_id
        else:
            self.session = SimpleNamespace(previous_response_id=preserved_previous_response_id)
        if preserved_previous_response_id:
            self._response_chain_previous_id = preserved_previous_response_id
            if self.session:
                self.session.previous_response_id = preserved_previous_response_id
            await _set_run_previous_response_id(
                self.run_id or "", preserved_previous_response_id
            )
        logger.info(
            "Switched model candidate run=%s candidate_index=%s provider=%s model=%s transport=%s",
            self.run_id,
            candidate_index,
            self.provider,
            self.model_name,
            self.transport,
        )
        return True

    async def connect(self):
        logger.info(
            "CONSUMER CONNECT provider=%s transport=%s model=%s run=%s consumer=%s channel_name=%s",
            getattr(self, "provider", None),
            self._transport_label() if hasattr(self, "transport") else "unknown",
            getattr(self, "model_name", None),
            getattr(self, "run_id", None),
            id(self),
            getattr(self, "channel_name", None),
        )
        user = self.scope.get("user")
        logger.info("connect START user=%s", getattr(user, "id", None))
        slug = self.scope.get("url_route", {}).get("kwargs", {}).get("slug")

        if not user or not getattr(user, "is_authenticated", False) or not slug:
            logger.info(
                "Connect exited due to missing auth/slug", extra={"user": str(user), "slug": slug}
            )
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
        reused_existing_run = False
        restored_previous_response_id = False
        hydrated_previous_response_id = ""

        logger.info(
            "connect parsed requested_run_id=%s raw_qs=%s",
            requested_run_id,
            (self.scope.get("query_string") or b"").decode("utf-8", errors="ignore"),
        )

        if requested_run_id:
            try:
                run_uuid = uuid.UUID(str(requested_run_id))
                run = await _fetch_agent_run(agent, user, run_uuid)
                if run:
                    reused_existing_run = True
                    logger.info(
                        "Reusing existing run from querystring",
                        extra={"agent": str(agent.id), "run": str(run.id)},
                    )
            except Exception as e:
                logger.exception(f"Exception while reusing run from querystring:  {e}")
                run = None

        if run is None:
            try:
                run = await _create_agent_run(agent, user, timezone.now())
                logger.info(
                    "Created new run",
                    extra={"agent": str(agent.id), "run": str(run.id)},
                )
            except Exception as e:
                logger.exception(f"Exception while creating new run: {e}")

        self.run = run
        self.run_id = str(run.id)
        hydrated_previous_response_id = (
            str(getattr(run, "previous_response_id", "") or "").strip() or None
        )
        self._response_chain_previous_id = hydrated_previous_response_id
        self._agents_md_bootstrap_complete = bool(
            getattr(run, "agents_md_bootstrap_complete", False)
        )
        await self._hydrate_agents_md_bootstrap_state()
        layer = getattr(self, "channel_layer", None)
        logger.info(
            "connect channel_layer=%s channel_name=%s run_id=%s",
            layer.__class__.__name__ if layer else None,
            getattr(self, "channel_name", None),
            self.run_id,
        )
        resumed_model_name = None
        if reused_existing_run:
            resumed_model_name = await _get_last_assistant_model_for_run(self.run_id)
        profile = await _get_profile(agent.policy_name)
        configured_provider = (
            profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
        )
        model_name = profile.model if profile else agent.default_model
        provider = normalize_provider_for_model(configured_provider, model_name)
        if provider != configured_provider:
            logger.info(
                "Provider adjusted from %s to %s based on model %s",
                configured_provider,
                provider,
                model_name,
            )
        self.client = None
        self.session = None
        self.transport = "http"
        self.use_ws = False
        self._model_candidates = await _get_model_failover_candidates(agent, provider, model_name)
        if not self._model_candidates:
            self._model_candidates = [
                {"provider": provider, "model": model_name, "source": "primary"}
            ]
        self._backup_retry_policy = await _get_backup_retry_policy(agent)
        self._active_model_candidate_index = 0
        if resumed_model_name:
            resumed_provider = normalize_provider_for_model(provider, resumed_model_name)
            for index, candidate in enumerate(self._model_candidates):
                candidate_provider = str(candidate.get("provider") or "").strip().lower()
                candidate_model = str(candidate.get("model") or "").strip()
                if candidate_provider == resumed_provider and candidate_model == resumed_model_name:
                    logger.info(
                        "Restoring active model candidate from run history agent=%s run=%s provider=%s model=%s candidate_index=%s",
                        agent.slug,
                        self.run_id,
                        resumed_provider,
                        resumed_model_name,
                        index,
                    )
                    if index > 0:
                        self._model_candidates = (
                            self._model_candidates[index:] + self._model_candidates[:index]
                        )
                    self._active_model_candidate_index = 0
                    break
        await self._switch_model_candidate(self._active_model_candidate_index)
        if self.use_ws:
            session_previous_response_id = _normalize_provider_response_id(
                self.provider,
                getattr(self.session, "previous_response_id", ""),
            )
            if hydrated_previous_response_id and not session_previous_response_id:
                self.session.previous_response_id = hydrated_previous_response_id or ""
                restored_previous_response_id = True
                logger.info(
                    "Restored persisted previous_response_id for agent=%s run=%s previous_response_id=%s",
                    agent.slug,
                    self.run_id,
                    hydrated_previous_response_id,
                )
            elif (
                session_previous_response_id
                and session_previous_response_id != hydrated_previous_response_id
            ):
                await _set_run_previous_response_id(self.run_id, session_previous_response_id)
        else:
            if self.session is None:
                self.session = SimpleNamespace(previous_response_id=hydrated_previous_response_id)
            else:
                self.session.previous_response_id = hydrated_previous_response_id
            logger.info(
                "Transport ready for provider=%s agent=%s run=%s model=%s",
                self.provider,
                agent.slug,
                self.run_id,
                self.model_name,
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
        logger.info(
            "AgentChatConsumer effective tools agent=%s run=%s tools=%s",
            agent.slug,
            self.run_id,
            [entry.tool.name for entry in effective_tools],
        )

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
            self.session_tools = self._format_ws_tool_definitions(tool_payloads)
        else:
            self.session_tools = []

        self.history = []
        handoff_payload = await _get_run_handoff(self.run_id)
        handoff_system_note = build_handoff_system_note(handoff_payload or {})
        if handoff_system_note:
            self.history.append(
                {"role": "system", "content": handoff_system_note, "_handoff_context": True}
            )
        self._ensure_system_context()
        await self._hydrate_artifact_context_from_events()

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
                "Transport group add OK run_group=%s approvals_group=%s",
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
                logger.info(
                    "AgentChatConsumer.connect DIAG sending debug_group_echo to %s", run_group
                )
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
        transport_label = self._transport_label()
        await self.send_json(
            {
                "type": "connected",
                "provider": self.provider,
                "provider_label": self.provider_label,
                "system_context": self.system_context or "",
                "tools": self.tools_meta,
                "transport_status": transport_status,
                "transport": self.transport,
                "transport_label": transport_label,
                "run_id": self.run_id,
                "model": self.model_name,
                "approval_grants": await _get_active_tool_approval_grants(self.run_id),
                "run_status": run.status,
                "handoff": handoff_payload or None,
            }
        )
        transport_detail = self._transport_name()
        transport_runtime = self._transport_label()
        if reused_existing_run:
            reconnect_text = f"{self.provider_label} {self.model_name} {transport_detail} run reconnected after a local browser disconnect."
            if self.use_ws and (
                restored_previous_response_id
                or str(getattr(self.session, "previous_response_id", "") or "").strip()
            ):
                reconnect_text += (
                    " Responses continuity was restored from our saved provider cursor."
                )
            elif self.use_ws:
                reconnect_text += " Responses continuity cursor was not available, so the next turn may feel colder."
            await self.send_json(
                {
                    "type": "system",
                    "kind": "connection",
                    "text": reconnect_text,
                    "timestamp": timezone.now().isoformat(),
                }
            )
        system_detail = f"{self.provider_label} provider '{self.model_name}' transport '{transport_detail}' connected on run {self.run_id}."
        await self.send_json(
            {
                "type": "system",
                "kind": "connection",
                "text": system_detail,
                "timestamp": timezone.now().isoformat(),
                "provider": self.provider,
                "provider_label": self.provider_label,
                "model": self.model_name,
                "transport": self.transport,
                "transport_label": transport_runtime,
            }
        )
        await self._sync_outstanding_provider_call_state()
        await self._flush_pending_tool_results()

    async def disconnect(self, close_code):
        provider = self.provider or "Provider"
        transport_label = self._transport_label()
        logger.info(
            "CONSUMER DISCONNECT provider=%s transport=%s model=%s run=%s consumer=%s channel_name=%s close_code=%s tool_result_flow=redis",
            provider,
            transport_label,
            self.model_name or "",
            getattr(self, "run_id", None),
            id(self),
            getattr(self, "channel_name", None),
            close_code,
        )
        if self.use_ws and self.client and self.run_id:
            agent_slug = self.agent.slug if self.agent else "unknown"
            logger.info(
                "Preserving session across browser disconnect for provider=%s transport=%s agent=%s run=%s previous_response_id=%s",
                self.provider_label,
                transport_label,
                agent_slug,
                self.run_id,
                str(getattr(self.session, "previous_response_id", "") or "").strip(),
            )
        channel_layer = self.channel_layer
        channel_name = getattr(self, "channel_name", None)
        logger.info(
            "Transport disconnect close_code=%s provider=%s transport=%s run_id=%s channel_name=%s run_group=%s approvals_group=%s",
            close_code,
            provider,
            transport_label,
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
            await self._accept_user_message(
                raw_text, persist=True, emit_message=False, mirror_to_transport=True
            )
            return
        if message_type == "tool_disconnect":
            user = self.scope.get("user")
            user_label = getattr(user, "username", "unknown")
            await self._log_transport_traffic(
                "tool_disconnect",
                {
                    "user": user_label,
                    "run_id": self.run_id,
                    "agent": self.agent.slug if self.agent else "unknown",
                },
            )
            await self.send_json(
                {
                    "type": "system",
                    "kind": "connection",
                    "text": f"Disconnect requested by {user_label}. Closing {self.provider_label} session.",
                    "timestamp": timezone.now().isoformat(),
                }
            )
            logger.info(
                "Disconnect requested via transport for agent=%s provider=%s transport=%s run=%s",
                user_label,
                self.provider_label,
                self._transport_label(),
                self.run_id,
            )
            # TODO: integrate tool_disconnect events with the approvals/channel layer if needed.
            await self.close(code=1000)
            return
        if message_type == "tool_approve":
            tool_call_id = content.get("tool_call_id")
            if tool_call_id:
                try:
                    tool_call = await sync_to_async(approve_tool_call)(
                        tool_call_id=tool_call_id,
                        user=self.scope.get("user"),
                        grant_mode=str(content.get("grant_mode") or "once"),
                    )
                except Exception as exc:
                    if "already acted on" not in str(exc).lower():
                        raise
                    logger.info(
                        "Ignoring duplicate tool approval run=%s tool_call_id=%s reason=%s",
                        self.run_id,
                        tool_call_id,
                        exc,
                    )
                    tool_call = await sync_to_async(
                        lambda: ToolCall.objects.only("id", "status").get(id=tool_call_id)
                    )()
                await self.send_json(
                    {
                        "type": "tool_call_approval_ack",
                        "run_id": str(self.run_id) if self.run_id else "",
                        "tool_call_id": str(tool_call.id),
                        "status": str(tool_call.status),
                        "timestamp": timezone.now().isoformat(),
                    }
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
            AgentRun.Status.WAITING_FOR_SUBRUN,
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
        prompt_text = raw_text
        log_text = raw_text
        secret_types = []
        if _should_scrub_prompts():
            log_text, secret_types = _scrub_input_text(raw_text)
            if secret_types:
                secret_list = ", ".join(sorted(set(secret_types)))
                summary_text = f"Maestro masked {len(secret_types)} secret(s) ({secret_list}) in logs and has your back."
                await self.send_json(
                    {
                        "type": "system",
                        "kind": "security",
                        "text": summary_text,
                        "timestamp": timezone.now().isoformat(),
                    }
                )
                logger.info(
                    "Masked secrets in logs for message run=%s secrets=%s",
                    self.run_id,
                    secret_list,
                )
        bootstrap_result = None
        if self.run_id and self.agent:
            await self._hydrate_artifact_context_from_events()
            bootstrap_result = await _bootstrap_memory_for_first_turn(
                str(self.run_id),
                str(self.agent.id),
                prompt_text,
            )
        explicit_memory = None
        if self.run_id:
            scope = getattr(self, "scope", {}) or {}
            scope_user = scope.get("user") if isinstance(scope, dict) else None
            user_id = getattr(scope_user, "id", None) or getattr(self.run, "started_by_id", None)
            explicit_memory = await _capture_explicit_user_memory(
                str(self.run_id),
                user_id,
                prompt_text,
            )
        if bootstrap_result and bootstrap_result.summary_text:
            self.history.append({"role": "system", "content": bootstrap_result.summary_text})
        if explicit_memory is not None:
            logger.info(
                "Stored explicit user memory during chat intake run=%s memory_id=%s",
                self.run_id,
                explicit_memory.id,
            )
        self.history.append({"role": "user", "content": prompt_text})
        if persist:
            await _persist_chat_history_event(self.run_id or "", "user", prompt_text)
        if emit_message:
            await self.send_json(
                {
                    "type": "message",
                    "role": "operator",
                    "direction": "out",
                    "author": author_label or ("You via Telegram" if source_transport else "You"),
                    "source_transport": source_transport or "",
                    "text": prompt_text,
                    "timestamp": timezone.now().isoformat(),
                }
            )
        if mirror_to_transport and not source_transport and self.agent:
            mirror_enabled = await sync_to_async(get_agent_telegram_mirror_enabled)(self.agent)
            if mirror_enabled:
                user = self.scope.get("user")
                await sync_to_async(send_run_transport_message)(
                    run_id=str(self.run_id),
                    text=prompt_text,
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
        if run_status in {
            AgentRun.Status.CANCELED,
            AgentRun.Status.COMPLETED,
            AgentRun.Status.FAILED,
        }:
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
        if self.run_id:
            await self._hydrate_artifact_context_from_events()
        self._ensure_system_context()
        async with self._send_lock:
            if self.use_ws:
                await self._dispatch_to_provider_ws()
            else:
                await self._dispatch_to_provider_http()

    async def _sync_outstanding_provider_call_state(self) -> set[str]:
        if not self.run_id:
            self._pending_provider_call_ids.clear()
            self._awaiting_tool_output = False
            return set()
        try:
            outstanding_provider_call_ids = set(
                await _get_outstanding_provider_call_ids(str(self.run_id))
            )
        except RuntimeError as exc:
            if "Database access not allowed" in str(exc):
                logger.debug(
                    "Skipping outstanding provider call sync because database access is unavailable run=%s",
                    self.run_id,
                )
                return set(self._pending_provider_call_ids)
            raise
        self._pending_provider_call_ids = set(outstanding_provider_call_ids)
        self._awaiting_tool_output = bool(outstanding_provider_call_ids)
        if outstanding_provider_call_ids:
            self._tool_result_event.clear()
        elif not self._tool_result_event.is_set():
            self._tool_result_event.set()
        logger.info(
            "Synced outstanding provider call ids run=%s provider_call_ids=%s",
            self.run_id,
            sorted(outstanding_provider_call_ids),
        )
        return outstanding_provider_call_ids

    async def _dispatch_to_provider_ws(self):
        if await self._dispatch_blocked_by_run_status():
            return
        if not self.session:
            logger.error("Error in consumers._dispatch_to_provider_ws:  No session established")
            return
        tools = self.session_tools if self.session_tools else None
        reconnect_attempts = 0
        max_reconnects = 1
        logger.debug(
            "Transport dispatch run=%s transport=%s tools=%s",
            self.run_id,
            self._transport_label(),
            bool(tools),
        )
        while True:
            if await self._dispatch_blocked_by_run_status():
                return
            await self._sync_outstanding_provider_call_state()
            if not self._tool_result_event.is_set():
                logger.info(
                    "Transport dispatch waiting for tool output run=%s transport=%s pending_tool_call_id=%s provider_call_id=%s ts=%s",
                    self.run_id,
                    self._transport_label(),
                    self._pending_tool_call_id,
                    self._pending_provider_call_id,
                    timezone.now().isoformat(),
                )
                logger.info(
                    "Transport dispatch yielding to push handler run=%s transport=%s pending_tool_call_id=%s provider_call_id=%s",
                    self.run_id,
                    self._transport_label(),
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
                "Transport payload run=%s transport=%s inputs=%s previous=%s tools=%s",
                self.run_id,
                self._transport_label(),
                len(input_items),
                self._current_previous_response_id(),
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
            previous_id = self._current_previous_response_id()
            if previous_id:
                payload_snapshot["previous_response_id"] = previous_id
            if self._last_tool_call_id:
                logger.info(
                    "Transport response.create after tool call run=%s transport=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s tool_output_ready=%s tool_output_batch_size=%s ts=%s",
                    self.run_id,
                    self._transport_label(),
                    self._last_tool_call_id,
                    self._last_provider_call_id,
                    self._current_previous_response_id(),
                    bool(self._tool_output_payloads),
                    len(self._tool_output_payloads),
                    timezone.now().isoformat(),
                )
            logger.info(
                "Transport input summary run=%s transport=%s summary=%s",
                self.run_id,
                self._transport_label(),
                self._summarize_ws_input_items(input_items),
            )
            await self._log_transport_traffic(
                f"{self._transport_label()} SEND",
                payload_snapshot,
            )
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
                    f"{self._transport_label()} RCV",
                    {
                        "response": response.get("raw") if isinstance(response, dict) else response,
                        "text": response.get("text") if isinstance(response, dict) else None,
                        "tool_calls": response.get("tool_calls")
                        if isinstance(response, dict)
                        else None,
                        "response_id": response.get("response_id")
                        if isinstance(response, dict)
                        else None,
                    },
                )
                logger.debug(
                    "Transport response run=%s transport=%s status=%s tool_calls=%s",
                    self.run_id,
                    self._transport_label(),
                    response.get("status"),
                    len(response.get("tool_calls") or []),
                )
                response_id = str(response.get("response_id") or "").strip()
                if response_id:
                    normalized_response_id = _normalize_provider_response_id(
                        self.provider, response_id
                    )
                    if normalized_response_id:
                        self._response_chain_previous_id = normalized_response_id
                        if self.session:
                            self.session.previous_response_id = normalized_response_id
                        await _set_run_previous_response_id(
                            self.run_id or "", normalized_response_id
                        )
                if self.run_id and self._artifact_context_ids:
                    consumed_context = await _consume_artifact_contexts(
                        str(self.run_id),
                        sorted(self._artifact_context_ids),
                    )
                    if consumed_context:
                        consumed_ids = {
                            str(value or "").strip()
                            for value in consumed_context.get("artifact_ids") or []
                            if str(value or "").strip()
                        }
                        if consumed_ids:
                            self._consumed_artifact_context_ids.update(consumed_ids)
                            self._artifact_context_ids.difference_update(consumed_ids)
                            self.history = [
                                entry
                                for entry in self.history
                                if str(entry.get("artifact_id") or "").strip() not in consumed_ids
                            ]
                if self._last_tool_call_id:
                    self._clear_tool_output_context()
                self._include_system_context = False
            except Exception as exc:
                if self._is_previous_response_not_found(exc):
                    logger.debug(
                        "WS previous_response_id not found run=%s transport=%s model=%s; resetting continuity and retrying once",
                        self.run_id,
                        self._transport_label(),
                        self.model_name,
                    )
                    await self._reset_response_continuity(
                        reason="previous response not found"
                    )
                    continue
                elif self._active_model_candidate_index + 1 < len(
                    self._model_candidates
                ) and is_retryable_model_failure(
                    exc,
                    client=self.client,
                    retry_policy=self._backup_retry_policy,
                ):
                    logger.warning(
                        "WS model failover run=%s transport=%s provider=%s model=%s next_provider=%s next_model=%s error=%s",
                        self.run_id,
                        self._transport_label(),
                        self.provider,
                        self.model_name,
                        self._model_candidates[self._active_model_candidate_index + 1]["provider"],
                        self._model_candidates[self._active_model_candidate_index + 1]["model"],
                        exc,
                    )
                    await self._switch_model_candidate(self._active_model_candidate_index + 1)
                    continue
                elif not self._is_ws_exception(exc):
                    logger.exception(
                        "Unexpected transport exception in dispatch run=%s transport=%s provider=%s model=%s",
                        self.run_id,
                        self._transport_label(),
                        self.provider_label,
                        self.model_name,
                    )
                    await self._send_error_and_abort(exc)
                    return

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
            assistant_text = response.get("text") or ""
            if not tool_calls and assistant_text:
                synthesized_tool_calls = extract_code_like_tool_calls(
                    assistant_text, self._tool_names()
                )
                if synthesized_tool_calls:
                    logger.warning(
                        "Repaired code-like tool output run=%s transport=%s provider=%s model=%s tool_names=%s",
                        self.run_id,
                        self._transport_label(),
                        self.provider,
                        self.model_name,
                        [call.get("name") for call in synthesized_tool_calls],
                    )
                    tool_calls = synthesized_tool_calls
                    assistant_text = ""
            if tool_calls:
                self._append_tool_call_history(assistant_text, tool_calls)
                for call in tool_calls:
                    logger.info(
                        "Transport tool call run=%s transport=%s name=%s call_id=%s",
                        self.run_id,
                        self._transport_label(),
                        call.get("name"),
                        call.get("call_id") or call.get("id"),
                    )
                    await self._handle_tool_call(call)
                continue

            logger.debug(
                "Transport assistant_text run=%s transport=%s present=%s",
                self.run_id,
                self._transport_label(),
                bool(response.get("text")),
            )
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
                if self.agent:
                    mirror_enabled = await sync_to_async(get_agent_telegram_mirror_enabled)(
                        self.agent
                    )
                    if mirror_enabled:
                        await sync_to_async(send_run_transport_message)(
                            run_id=str(self.run_id),
                            text=assistant_text,
                            author_label="assistant",
                            control_payload={"event_type": "assistant_message"},
                        )
                self._clear_tool_output_context()
                return

    async def _dispatch_to_provider_http(self):
        if await self._dispatch_blocked_by_run_status():
            return
        tools_available = self.tool_definitions if self.tool_definitions else None
        model_candidates = self._model_candidates or [
            {"provider": self.provider, "model": self.model_name or "unknown", "source": "primary"}
        ]
        candidate_index = min(self._active_model_candidate_index, len(model_candidates) - 1)
        retry_without_previous_response = False
        while candidate_index < len(model_candidates):
            moved_to_next_candidate = False
            if candidate_index != self._active_model_candidate_index:
                await self._switch_model_candidate(candidate_index)
                retry_without_previous_response = False
            model = self.model_name or "unknown"
            while True:
                if await self._dispatch_blocked_by_run_status():
                    return
                await self._sync_outstanding_provider_call_state()
                if not self._tool_result_event.is_set():
                    logger.info(
                        "HTTP dispatch waiting for tool output run=%s transport=%s pending_tool_call_id=%s provider_call_id=%s ts=%s",
                        self.run_id,
                        self._transport_label(),
                        self._pending_tool_call_id,
                        self._pending_provider_call_id,
                        timezone.now().isoformat(),
                    )
                    return
                snapshot_messages = [
                    {"role": entry.get("role"), "content": entry.get("content") or ""}
                    for entry in self.history[-4:]
                ]
                tools_to_send = tools_available if tools_available else None
                payload_snapshot: dict[str, object] = {
                    "model": model,
                    "messages": snapshot_messages,
                }
                if tools_to_send:
                    payload_snapshot["tools"] = tools_to_send
                await self._log_transport_traffic("HTTP SEND", payload_snapshot)
                try:
                    response = await retry_with_backoff(
                        lambda model=model, tools_to_send=tools_to_send: self.client.complete(
                            self.history,
                            model=model,
                            tools=tools_to_send,
                            previous_response_id=self._current_previous_response_id(),
                            outstanding_provider_call_ids=(
                                list(self._tool_output_provider_call_ids)
                                if self._tool_output_provider_call_ids
                                else None
                            ),
                        ),
                        max_retries=int(
                            self._backup_retry_policy.get("retry_same_model_attempts", 1) or 0
                        ),
                        is_transient_error=lambda exc: is_retryable_model_failure(
                            exc,
                            client=self.client,
                            retry_policy=self._backup_retry_policy,
                        ),
                    )
                except Exception as exc:
                    if (
                        not retry_without_previous_response
                        and getattr(self.client, "is_previous_response_not_found", None)
                        and self.client.is_previous_response_not_found(exc)
                    ):
                        logger.debug(
                            "HTTP previous_response_id not found run=%s transport=%s model=%s; resetting continuity and retrying once",
                            self.run_id,
                            self._transport_label(),
                            model,
                        )
                        retry_without_previous_response = True
                        await self._reset_response_continuity(
                            reason="previous response not found"
                        )
                        continue
                    if candidate_index + 1 < len(model_candidates) and is_retryable_model_failure(
                        exc,
                        client=self.client,
                        retry_policy=self._backup_retry_policy,
                    ):
                        logger.warning(
                            "HTTP model failover run=%s transport=%s provider=%s model=%s next_provider=%s next_model=%s error=%s",
                            self.run_id,
                            self._transport_label(),
                            self.provider,
                            model,
                            model_candidates[candidate_index + 1]["provider"],
                            model_candidates[candidate_index + 1]["model"],
                            exc,
                        )
                        candidate_index += 1
                        moved_to_next_candidate = True
                        retry_without_previous_response = False
                        await self._switch_model_candidate(candidate_index)
                        break
                    await self._send_http_error(exc)
                    return
                await self._log_transport_traffic(
                    "HTTP RCV",
                    {
                        "model": model,
                        "response": response.get("raw") if isinstance(response, dict) else response,
                        "text": response.get("text") if isinstance(response, dict) else None,
                        "tool_calls": response.get("tool_calls")
                        if isinstance(response, dict)
                        else None,
                        "response_id": response.get("response_id")
                        if isinstance(response, dict)
                        else None,
                    },
                )
                response_id = str(response.get("response_id") or "").strip()
                if response_id:
                    if self.session:
                        normalized_response_id = _normalize_provider_response_id(
                            self.provider, response_id
                        )
                        if normalized_response_id:
                            self._response_chain_previous_id = normalized_response_id
                            self.session.previous_response_id = normalized_response_id
                            await _set_run_previous_response_id(
                                self.run_id or "", normalized_response_id
                            )
                if self.run_id and self._artifact_context_ids:
                    consumed_context = await _consume_artifact_contexts(
                        str(self.run_id),
                        sorted(self._artifact_context_ids),
                    )
                    if consumed_context:
                        consumed_ids = {
                            str(value or "").strip()
                            for value in consumed_context.get("artifact_ids") or []
                            if str(value or "").strip()
                        }
                        if consumed_ids:
                            self._consumed_artifact_context_ids.update(consumed_ids)
                            self._artifact_context_ids.difference_update(consumed_ids)
                            self.history = [
                                entry
                                for entry in self.history
                                if str(entry.get("artifact_id") or "").strip() not in consumed_ids
                            ]
                tool_calls = response.get("tool_calls") or []
                assistant_text = response.get("text") or ""
                if not tool_calls and assistant_text:
                    synthesized_tool_calls = extract_code_like_tool_calls(
                        assistant_text, self._tool_names()
                    )
                    if synthesized_tool_calls:
                        logger.warning(
                            "Repaired code-like tool output run=%s transport=%s provider=%s model=%s tool_names=%s",
                            self.run_id,
                            self._transport_label(),
                            self.provider,
                            model,
                            [call.get("name") for call in synthesized_tool_calls],
                        )
                        tool_calls = synthesized_tool_calls
                        assistant_text = ""
                if tool_calls:
                    self._append_tool_call_history(assistant_text, tool_calls)
                    for call in tool_calls:
                        await self._handle_tool_call(call)
                    return

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
                    if self.agent:
                        mirror_enabled = await sync_to_async(get_agent_telegram_mirror_enabled)(
                            self.agent
                        )
                        if mirror_enabled:
                            await sync_to_async(send_run_transport_message)(
                                run_id=str(self.run_id),
                                text=assistant_text,
                                author_label="assistant",
                                control_payload={"event_type": "assistant_message"},
                            )
                    self._clear_tool_output_context()
                    return

                candidate_index += 1
                if moved_to_next_candidate:
                    continue
                break

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
        self._pending_provider_call_ids.add(call_id)
        args = self._parse_tool_args(call.get("arguments"))

        # if tool_name == "repo_tree":
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
            self._pending_provider_call_ids.discard(call_id)
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
            self._pending_provider_call_ids.discard(call_id)
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
            self._pending_provider_call_ids.discard(call_id)
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
                "tool_display_name": str((args or {}).get("display_label") or tool_name),
                "display_summary": str((args or {}).get("display_summary") or ""),
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

    def _append_tool_call_history(
        self, assistant_text: str, tool_calls: list[dict[str, object]]
    ) -> None:
        normalized_calls: list[dict[str, object]] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            normalized_calls.append(
                {
                    "id": str(
                        call.get("id") or call.get("call_id") or call.get("provider_call_id") or ""
                    ).strip(),
                    "name": str(call.get("name") or "").strip(),
                    "arguments": call.get("arguments") or {},
                }
            )
        if not normalized_calls:
            return
        entry: dict[str, object] = {"role": "assistant", "content": assistant_text or ""}
        entry["tool_calls"] = normalized_calls
        self.history.append(entry)

    def _clear_tool_output_context(self) -> None:
        self._last_tool_call_id = None
        self._last_provider_call_id = None
        self._tool_output_payload = None
        self._tool_output_payloads = []
        self._tool_output_provider_call_ids = []
        self._pending_provider_call_ids.clear()

    def _merge_tool_output_provider_call_ids(self, provider_call_ids: list[str]) -> None:
        merged: list[str] = list(self._tool_output_provider_call_ids)
        for provider_call_id in provider_call_ids:
            normalized = str(provider_call_id or "").strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
        self._tool_output_provider_call_ids = merged

    def _current_previous_response_id(self) -> str | None:
        raw_chain_id = getattr(self, "_response_chain_previous_id", None)
        raw_session_id = getattr(self.session, "previous_response_id", None)
        chain_id = _normalize_provider_response_id(self.provider, raw_chain_id)
        session_id = _normalize_provider_response_id(self.provider, raw_session_id)
        logger.debug(
            "Resolved previous_response_id run=%s raw_chain_id=%r raw_session_id=%r normalized_chain_id=%r normalized_session_id=%r",
            self.run_id,
            raw_chain_id,
            raw_session_id,
            chain_id,
            session_id,
        )
        if chain_id:
            return chain_id
        return session_id or None

    async def _hydrate_agents_md_bootstrap_state(self) -> None:
        if self._agents_md_bootstrap_complete or not self.run_id:
            return
        self._agents_md_bootstrap_complete = await _run_has_agents_md_bootstrap_complete(
            self.run_id
        )
        if self._agents_md_bootstrap_complete:
            try:
                await _set_run_agents_md_bootstrap_complete(self.run_id, True)
            except Exception:
                logger.exception(
                    "Failed to persist AGENTS.md bootstrap completion run=%s", self.run_id
                )
            logger.info("Restored AGENTS.md bootstrap completion run=%s", self.run_id)

    def _ensure_system_context(self) -> None:
        agent = self.agent
        if not agent:
            return
        context = build_system_context(
            agent,
            model_name=self.model_name or agent.default_model,
            transport=self.transport,
            tool_names=self._tool_names(),
            authenticated_user=getattr(self, "scope", {}).get("user")
            or getattr(self.run, "started_by", None),
            agents_md_bootstrap_complete=self._agents_md_bootstrap_complete,
        )
        self.system_context = context
        if not context:
            return
        for entry in self.history:
            if entry.get(self._system_context_marker):
                entry["content"] = context
                return
        self.history.insert(
            0, {"role": "system", "content": context, self._system_context_marker: True}
        )

    async def _hydrate_artifact_context_from_events(self) -> None:
        if not self.run_id:
            return
        try:
            events = await _get_artifact_context_events(str(self.run_id))
        except RuntimeError as exc:
            if "Database access not allowed" in str(exc):
                logger.debug(
                    "Skipping artifact context hydration because database access is unavailable run=%s",
                    self.run_id,
                )
                return
            raise
        removed_artifact_ids: set[str] = set()
        consumed_artifact_ids: set[str] = set()
        for row in events:
            event_type = str(row.get("event_type") or "").strip()
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            artifact_id = str(payload.get("artifact_id") or "").strip()
            artifact_ids = [
                str(value or "").strip()
                for value in (payload.get("artifact_ids") or [])
                if str(value or "").strip()
            ] if isinstance(payload.get("artifact_ids"), list) else []
            if event_type == "artifact_removed":
                if artifact_id:
                    removed_artifact_ids.add(artifact_id)
                    self._artifact_context_ids.discard(artifact_id)
                    self.history = [
                        entry
                        for entry in self.history
                        if str(entry.get("artifact_id") or "").strip() != artifact_id
                    ]
                continue
            if event_type == "artifact_consumed":
                if artifact_id:
                    consumed_artifact_ids.add(artifact_id)
                for value in artifact_ids:
                    consumed_artifact_ids.add(value)
                self._consumed_artifact_context_ids.update(consumed_artifact_ids)
                if consumed_artifact_ids:
                    self._artifact_context_ids.difference_update(consumed_artifact_ids)
                    self.history = [
                        entry
                        for entry in self.history
                        if str(entry.get("artifact_id") or "").strip() not in consumed_artifact_ids
                    ]
                continue
            if (
                not artifact_id
                or artifact_id in self._artifact_context_ids
                or artifact_id in removed_artifact_ids
                or artifact_id in consumed_artifact_ids
                or artifact_id in self._consumed_artifact_context_ids
            ):
                continue
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            artifact_path = str(payload.get("artifact_path") or "").strip()
            self.history.append(
                {
                    "role": "system",
                    "content": text,
                    "kind": "artifact_context",
                    "artifact_id": artifact_id,
                    "artifact_path": artifact_path,
                    "artifact_count": payload.get("artifact_count"),
                }
            )
            self._artifact_context_ids.add(artifact_id)
            logger.info(
                "Hydrated artifact context into history run=%s artifact_id=%s artifact_path=%s text_len=%d",
                self.run_id,
                artifact_id,
                artifact_path,
                len(text),
            )

    async def _flush_pending_tool_results(self, limit: int = 20) -> None:
        if not self.run_id:
            return
        await self._sync_outstanding_provider_call_state()
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
            if self._current_previous_response_id():
                try:
                    replayable, incomplete = await _get_replayable_tool_result_tail(self.run_id)
                except Exception as exc:
                    logger.exception(
                        "Failed to load replayable tool result tail run=%s",
                        self.run_id,
                        exc_info=exc,
                    )
                    replayable, incomplete = [], False
                if replayable:
                    logger.info(
                        "Replaying persisted tool results from run history run=%s tool_call_ids=%s",
                        self.run_id,
                        [entry.get("tool_call_id") for entry in replayable],
                    )
                    pending = replayable
                elif incomplete:
                    logger.warning(
                        "No replayable tool results remain for run=%s; resetting response continuity",
                        self.run_id,
                    )
                    await self._reset_response_continuity(
                        reason="missing persisted tool output after reconnect",
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
        agents_md_bootstrap_complete = self._agents_md_bootstrap_complete
        staged_provider_call_ids: list[str] = []
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
            result_data = (
                payload.get("result") or payload.get("stdout") or payload.get("stderr") or payload
            )
            tool_output_text = compact_tool_output_text(str(payload.get("tool_name") or "tool"), result_data)
            provider_call_id = (
                payload.get("provider_call_id") or self._pending_provider_call_id or ""
            )
            provider_call_id = str(provider_call_id).strip() or None
            previous_response_id = self._current_previous_response_id()
            should_stage_for_provider = bool(
                provider_call_id and (previous_response_id or self.provider != "openai")
            )
            if provider_call_id:
                self._pending_provider_call_ids.discard(provider_call_id)
            if should_stage_for_provider:
                staged_provider_call_ids.append(provider_call_id)
                self._tool_output_payloads.append(payload)
                self.history.append(
                    {
                        "role": "tool",
                        "content": tool_output_text,
                        "tool_call_id": tool_call_id,
                        "tool_name": str(payload.get("tool_name") or "tool").strip() or "tool",
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
            if not agents_md_bootstrap_complete:
                try:
                    tool_details = await _get_tool_call_details(str(tool_call_id or ""))
                except Exception:
                    logger.exception(
                        "Failed to inspect tool call for AGENTS.md bootstrap run=%s tool_call_id=%s",
                        self.run_id,
                        tool_call_id,
                    )
                    tool_details = None
                if tool_details:
                    tool_name = str(tool_details.get("tool_name") or "").strip()
                    status = str(tool_details.get("status") or "").strip().upper()
                    args = (
                        tool_details.get("args")
                        if isinstance(tool_details.get("args"), dict)
                        else {}
                    )
                    path_value = str((args or {}).get("path") or "").strip()
                    path_name = Path(path_value).name.upper() if path_value else ""
                    if (
                        tool_name == "file_read"
                        and status == ToolCall.Status.COMPLETED
                        and path_name == "AGENTS.MD"
                    ):
                        agents_md_bootstrap_complete = True
                        try:
                            await sync_to_async(append_event)(
                                run_id=self.run_id,
                                event_type=AGENTS_MD_BOOTSTRAP_EVENT,
                                payload={
                                    "tool_call_id": str(tool_call_id or ""),
                                    "tool_name": tool_name,
                                    "path": path_value,
                                    "status": status,
                                },
                                broadcast_to_run=False,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to persist AGENTS.md bootstrap marker run=%s tool_call_id=%s path=%s",
                                self.run_id,
                                tool_call_id,
                                path_value,
                            )
                        try:
                            await _set_run_agents_md_bootstrap_complete(self.run_id, True)
                        except Exception:
                            logger.exception(
                                "Failed to persist AGENTS.md bootstrap completion flag run=%s tool_call_id=%s path=%s",
                                self.run_id,
                                tool_call_id,
                                path_value,
                            )
                        logger.info(
                            "Marked AGENTS.md bootstrap complete run=%s tool_call_id=%s path=%s",
                            self.run_id,
                            tool_call_id,
                            path_value,
                        )
            if agents_md_bootstrap_complete:
                self._agents_md_bootstrap_complete = True
                self._include_system_context = False
        if last_payload:
            self._last_tool_call_id = last_payload.get("tool_call_id")
            api_provider_call_id = (
                last_payload.get("provider_call_id") or self._pending_provider_call_id
            )
            self._last_provider_call_id = api_provider_call_id
            self._tool_output_payload = last_payload
            self._merge_tool_output_provider_call_ids(staged_provider_call_ids)
            self._pending_tool_call_id = None
            self._pending_provider_call_id = None
            self._awaiting_tool_output = bool(self._pending_provider_call_ids)
            if self._pending_provider_call_ids:
                self._tool_result_event.clear()
            else:
                if not self._tool_result_event.is_set():
                    self._tool_result_event.set()
        elif self._awaiting_tool_output:
            logger.warning(
                "No provider-eligible tool outputs available after flush run=%s pending_tool_call_id=%s pending_provider_call_id=%s previous_response_id=%s",
                self.run_id,
                self._pending_tool_call_id,
                self._pending_provider_call_id,
                self._current_previous_response_id(),
            )
            self._tool_output_provider_call_ids = []
            self._pending_tool_call_id = None
            self._pending_provider_call_id = None
            self._awaiting_tool_output = bool(self._pending_provider_call_ids)
            if not self._pending_provider_call_ids and not self._tool_result_event.is_set():
                self._tool_result_event.set()

    async def _reset_response_continuity(self, *, reason: str) -> None:
        logger.warning(
            "Resetting response continuity run=%s reason=%s previous_response_id=%s pending_provider_call_ids=%s",
            self.run_id,
            reason,
            self._current_previous_response_id(),
            sorted(self._pending_provider_call_ids),
        )
        self._response_chain_previous_id = None
        if self.session:
            self.session.previous_response_id = None
        await _set_run_previous_response_id(self.run_id or "", None)
        self._clear_tool_output_context()
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
            tool_output_text = compact_tool_output_text(str(payload.get("tool_name") or "tool"), payload)
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
        self._tool_output_payloads.append(self._tool_output_payload)
        if provider_call_id not in self._tool_output_provider_call_ids:
            self._tool_output_provider_call_ids.append(provider_call_id)
        self._pending_provider_call_ids.discard(provider_call_id)
        self._pending_tool_call_id = None
        self._pending_provider_call_id = None
        self._awaiting_tool_output = bool(self._pending_provider_call_ids)
        if not self._pending_provider_call_ids and not self._tool_result_event.is_set():
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
        run_id_from_event = (
            parsed_event.get("run_id") or raw_event.get("run_id") or data.get("run_id")
        )
        tool_call_id = data.get("tool_call_id") or data.get("call_id")
        provider_call_id = data.get("provider_call_id")
        current_run_id = str(self.run_id or "").strip()
        event_run_id = str(run_id_from_event or "").strip()

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

        if current_run_id and event_run_id and event_run_id != current_run_id:
            logger.warning(
                "stale_run_event dropping event current_run_id=%s event_run_id=%s event_type=%s tool_call_id=%s provider_call_id=%s",
                current_run_id,
                event_run_id,
                event_type,
                tool_call_id,
                provider_call_id,
            )
            return

        if event_type == "tool_result_ready":
            logger.info(
                "Event type -> tool_result_ready AgentChatConsumer.push tooling run=%s detected pending tool results",
                getattr(self, "run_id", None),
            )
            await self._flush_pending_tool_results()
            if self._tool_output_payloads and not self._pending_provider_call_ids:
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

        if event_type == "tool_call_requested":
            logger.info(
                "4:  Event type -> tool_call_requested AgentChatConsumer.push normalized to tool_request run=%s data=%s",
                getattr(self, "run_id", None),
                data,
            )
            await self.send_json({"type": "tool_request", **data})
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

        if event_type == "artifact_context":
            text = str(data.get("text") or "").strip()
            artifact_id = str(data.get("artifact_id") or "").strip()
            artifact_path = str(data.get("artifact_path") or "").strip()
            if text:
                if artifact_id:
                    self._artifact_context_ids.add(artifact_id)
                self.history.append(
                    {
                        "role": "system",
                        "content": text,
                        "kind": "artifact_context",
                        "artifact_id": artifact_id,
                        "artifact_path": artifact_path,
                        "artifact_count": data.get("artifact_count"),
                    }
                )
            logger.info(
                "4:  Event type -> artifact_context AgentChatConsumer.push captured run=%s artifact_count=%s text_len=%s artifact_path=%s",
                getattr(self, "run_id", None),
                data.get("artifact_count"),
                len(text),
                artifact_path,
            )
            return

        if event_type == "remote_ops_message":
            text = str(data.get("text") or "")
            kind = str(data.get("kind") or "remote_ops").strip()
            if text and kind not in {"artifact_upload", "artifact_delete"}:
                self.history.append(
                    {
                        "role": "system",
                        "content": text,
                        "kind": kind,
                    }
                )
            await self.send_json(
                {
                    "type": "message",
                    "role": "system",
                    "author": str(data.get("author_label") or "system").strip().lower(),
                    "kind": kind,
                    "text": text,
                    "timestamp": data.get("timestamp") or timezone.now().isoformat(),
                }
            )
            return

        if event_type == "artifact_removed":
            artifact_id = str(data.get("artifact_id") or "").strip()
            if artifact_id:
                self._artifact_context_ids.discard(artifact_id)
                self._consumed_artifact_context_ids.discard(artifact_id)
                self.history = [
                    entry
                    for entry in self.history
                    if str(entry.get("artifact_id") or "").strip() != artifact_id
                ]
            logger.info(
                "4:  Event type -> artifact_removed AgentChatConsumer.push captured run=%s artifact_id=%s",
                getattr(self, "run_id", None),
                artifact_id,
            )
            return

        if event_type == "artifact_consumed":
            artifact_ids = [
                str(value or "").strip()
                for value in (data.get("artifact_ids") or [])
                if str(value or "").strip()
            ] if isinstance(data.get("artifact_ids"), list) else []
            artifact_id = str(data.get("artifact_id") or "").strip()
            if artifact_id:
                artifact_ids.append(artifact_id)
            if artifact_ids:
                artifact_id_set = set(artifact_ids)
                self._artifact_context_ids.difference_update(artifact_id_set)
                self._consumed_artifact_context_ids.update(artifact_id_set)
                self.history = [
                    entry
                    for entry in self.history
                    if str(entry.get("artifact_id") or "").strip() not in artifact_id_set
                ]
            logger.info(
                "4:  Event type -> artifact_consumed AgentChatConsumer.push captured run=%s artifact_ids=%s",
                getattr(self, "run_id", None),
                artifact_ids,
            )
            await self.send_json(
                {
                    "type": "artifact_consumed",
                    "artifact_ids": artifact_ids,
                    "artifacts": data.get("artifacts") or [],
                    "timestamp": data.get("timestamp") or timezone.now().isoformat(),
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
        previous_response_id = self._current_previous_response_id()
        return build_input_items(
            self.history,
            previous_response_id=previous_response_id,
            outstanding_provider_call_ids=self._tool_output_provider_call_ids or None,
            run_id=self.run_id,
        )

    def _build_ws_input_items(self) -> list[dict[str, object]]:
        return build_ws_request_input_items(
            self.history,
            previous_response_id=self._current_previous_response_id(),
            outstanding_provider_call_ids=self._tool_output_provider_call_ids or None,
            include_system_context=self._include_system_context,
            last_user_text=self._last_user_message(),
            run_id=self.run_id,
        )

    def _last_user_message(self) -> str | None:
        for entry in reversed(self.history):
            if entry.get("role") != "user":
                continue
            content = (entry.get("content") or "").strip()
            if content:
                return content
        return None

    @staticmethod
    def _extract_input_item_text(item: dict[str, object]) -> str:
        content_items = item.get("content", [])
        if isinstance(content_items, list):
            text = " ".join(
                str(entry.get("text") or "")
                for entry in content_items
                if isinstance(entry, dict)
            ).strip()
            if text:
                return text
        output_text = str(item.get("output") or "").strip()
        if output_text:
            return output_text
        return str(item.get("content") or "").strip()

    @staticmethod
    def _summarize_ws_input_items(input_items: list[dict[str, object]]) -> dict[str, object]:
        role_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        role_text_counts: dict[str, Counter[str]] = {
            "system": Counter(),
            "user": Counter(),
            "assistant": Counter(),
            "tool": Counter(),
        }
        artifact_context_texts: Counter[str] = Counter()

        for item in input_items:
            role = str(item.get("role") or "").strip() or "<missing>"
            item_type = str(item.get("type") or "").strip() or "<missing>"
            role_counts[role] += 1
            type_counts[item_type] += 1

            text = AgentChatConsumer._extract_input_item_text(item)
            normalized_text = " ".join(text.split())
            if not normalized_text:
                continue
            if role in role_text_counts:
                role_text_counts[role][normalized_text] += 1
            if role == "system" and normalized_text.startswith("ATTACHED FILE CONTEXT"):
                artifact_context_texts[normalized_text] += 1

        duplicate_text_entries = sum(
            count - 1
            for role_counts_by_text in role_text_counts.values()
            for count in role_counts_by_text.values()
            if count > 1
        )
        repeated_artifact_context_items = sum(
            count - 1 for count in artifact_context_texts.values() if count > 1
        )

        return {
            "items": len(input_items),
            "role_counts": dict(role_counts),
            "type_counts": dict(type_counts),
            "system_items": role_counts.get("system", 0),
            "user_items": role_counts.get("user", 0),
            "assistant_items": role_counts.get("assistant", 0),
            "tool_items": role_counts.get("tool", 0),
            "function_call_output_items": type_counts.get("function_call_output", 0),
            "artifact_context_items": role_counts.get("system", 0)
            and sum(
                1
                for item in input_items
                if str(item.get("role") or "").strip() == "system"
                and AgentChatConsumer._extract_input_item_text(item)
                .lstrip()
                .startswith("ATTACHED FILE CONTEXT")
            ),
            "repeated_artifact_context_items": repeated_artifact_context_items,
            "repeated_user_text_entries": sum(
                count - 1 for count in role_text_counts["user"].values() if count > 1
            ),
            "repeated_system_text_entries": sum(
                count - 1 for count in role_text_counts["system"].values() if count > 1
            ),
            "repeated_tool_text_entries": sum(
                count - 1 for count in role_text_counts["tool"].values() if count > 1
            ),
            "duplicate_text_entries": duplicate_text_entries,
        }

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
        transport_label = self._transport_label()
        logger.warning(
            "%s %s reconnect triggered run=%s agent=%s reason=%s summary=%s",
            self.provider_label,
            transport_label,
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
            message = (
                f"{self.provider_label} {transport_label} connection hiccup: {exc}. "
                f"Request_id={request_id}"
            )
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
        provider = self.provider_label or "Provider"
        model = self.model_name or "unknown"
        transport_label = self._transport_label()
        logger.error(
            "%s %s (%s) giving up after retries for run=%s: %s",
            provider,
            transport_label,
            model,
            self.run_id,
            exc,
        )
        await self.send_json(
            {
                "type": "error",
                "message": str(exc),
                "timestamp": timezone.now().isoformat(),
            }
        )

    async def _send_http_error(self, exc: Exception) -> None:
        provider = self.provider_label or "Provider"
        model = self.model_name or "unknown"
        logger.error(
            "%s HTTP call failed for run=%s model=%s agent=%s: %s",
            provider,
            self.run_id,
            model,
            self.agent.slug if self.agent else "unknown",
            exc,
        )
        await self.send_json(
            {
                "type": "error",
                "message": str(exc),
                "timestamp": timezone.now().isoformat(),
            }
        )

    def _format_ws_tool_definitions(
        self, tool_payloads: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        formatter = getattr(self.client, "format_tool_definitions_for_responses", None)
        if not callable(formatter):
            return []
        try:
            response = formatter(tool_payloads)
            return list(response) if response else []
        except Exception:
            logger.exception(
                "Unable to format provider transport tool definitions run=%s provider=%s model=%s",
                self.run_id,
                self.provider_label,
                self.model_name,
            )
            return []

    def _is_ws_exception(self, exc: Exception) -> bool:
        is_ws_exception = getattr(self.client, "is_ws_exception", None)
        if callable(is_ws_exception):
            try:
                return bool(is_ws_exception(exc))
            except Exception:
                logger.exception(
                    "Failed to evaluate ws exception for provider=%s model=%s",
                    self.provider_label,
                    self.model_name,
                )
        return False

    def _is_previous_response_not_found(self, exc: Exception) -> bool:
        is_prev_not_found = getattr(self.client, "is_previous_response_not_found", None)
        if callable(is_prev_not_found):
            try:
                return bool(is_prev_not_found(exc))
            except Exception:
                logger.exception(
                    "Failed to evaluate previous-response-not-found check for provider=%s model=%s",
                    self.provider_label,
                    self.model_name,
                )
        return False
