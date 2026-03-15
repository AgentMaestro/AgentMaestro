from __future__ import annotations

import hmac
import json
import logging
import time as time_module
import uuid
from datetime import datetime, time, timedelta, timezone as dt_timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from comms.services.outbound import send_conversation_message
from memory.models import MemoryRecord, ScheduledTask
from memory.services import remember

logger = logging.getLogger(__name__)

MAX_RESULT_SUMMARY_CHARS = 2000
DEFAULT_SCHEDULED_TASK_LIMIT = 10
DEFAULT_WEATHER_MAX_RESULTS = 5
DEFAULT_WEATHER_MAX_CHARS = 4000
_DEFAULT_TOOLRUNNER_SANDBOX_ROOT = Path("C:/tmp/agentmaestro/sandbox")

SCHEDULED_TASK_CREATED_SOURCE_KIND = "scheduled_task_created"
SCHEDULED_TASK_EXECUTED_SOURCE_KIND = "scheduled_task_executed"
SCHEDULED_TASK_EXEC_BUCKET_PREFIX = "scheduled-task-exec-bucket"


class ScheduledTaskError(RuntimeError):
    pass


class ScheduledTaskConfigurationError(ScheduledTaskError):
    pass


class ScheduledTaskExecutionError(ScheduledTaskError):
    pass


def create_scheduled_task(
    *,
    agent,
    owner,
    task_type: str,
    local_time_value: str | time,
    timezone_name: str,
    title: str = "",
    execution_payload: dict | None = None,
    delivery_target: str = ScheduledTask.DeliveryTarget.PAIRED_TRANSPORT,
    source_memory: MemoryRecord | None = None,
    enabled: bool = True,
) -> ScheduledTask:
    scheduled_task_id = uuid.uuid4()
    normalized_time = _normalize_local_time(local_time_value)
    normalized_timezone = _normalize_timezone_name(timezone_name)
    normalized_payload = dict(execution_payload or {})
    next_run_at = compute_next_run_at(normalized_time, normalized_timezone)

    if source_memory is None:
        source_memory = remember(
            agent=agent,
            memory_kind=MemoryRecord.MemoryKind.EPISODIC,
            content=_build_schedule_memory_content(
                task_type=task_type,
                timezone_name=normalized_timezone,
                local_time_value=normalized_time,
                execution_payload=normalized_payload,
            ),
            tags=["scheduled-task", task_type.replace("_", "-")],
            summary=title or _default_task_title(task_type, normalized_payload),
            importance=0.65,
            source_kind=SCHEDULED_TASK_CREATED_SOURCE_KIND,
            source_ref=str(scheduled_task_id),
            dedupe_key=f"scheduled-task-created:{scheduled_task_id}",
            dedupe_mode="key",
        )

    return ScheduledTask.objects.create(
        id=scheduled_task_id,
        workspace=agent.workspace,
        agent=agent,
        owner=owner,
        source_memory=source_memory,
        title=title or _default_task_title(task_type, normalized_payload),
        task_type=task_type,
        schedule_kind=ScheduledTask.ScheduleKind.DAILY_TIME,
        delivery_target=delivery_target,
        timezone=normalized_timezone,
        local_time=normalized_time,
        next_run_at=next_run_at,
        enabled=enabled,
        execution_payload=normalized_payload,
    )


def list_scheduled_tasks(*, agent=None, workspace=None, owner=None, enabled_only: bool = False, limit: int = 20):
    queryset = ScheduledTask.objects.select_related("agent", "owner", "workspace", "source_memory")
    if agent is not None:
        queryset = queryset.filter(agent=agent)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    return list(queryset.order_by("next_run_at", "created_at")[: max(1, min(int(limit or 20), 50))])


@transaction.atomic
def claim_due_scheduled_tasks(*, now: datetime | None = None, limit: int = DEFAULT_SCHEDULED_TASK_LIMIT) -> list[ScheduledTask]:
    current_time = now or timezone.now()
    due_tasks = list(
        ScheduledTask.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=current_time)
        .select_related("agent", "owner", "workspace", "source_memory")
        .order_by("next_run_at", "created_at")[: max(1, min(int(limit or DEFAULT_SCHEDULED_TASK_LIMIT), 50))]
    )
    for scheduled_task in due_tasks:
        scheduled_task.last_run_at = current_time
        scheduled_task.next_run_at = compute_next_run_at(
            scheduled_task.local_time,
            scheduled_task.timezone,
            now=current_time + timedelta(seconds=1),
        )
        scheduled_task.save(update_fields=["last_run_at", "next_run_at", "updated_at"])
    return due_tasks


@transaction.atomic
def mark_scheduled_task_success(scheduled_task: ScheduledTask, summary: str) -> ScheduledTask:
    scheduled_task.last_success_at = timezone.now()
    scheduled_task.last_result_summary = _trim_text(summary, MAX_RESULT_SUMMARY_CHARS)
    scheduled_task.last_error = ""
    scheduled_task.failure_count = 0
    scheduled_task.save(
        update_fields=[
            "last_success_at",
            "last_result_summary",
            "last_error",
            "failure_count",
            "updated_at",
        ]
    )
    return scheduled_task


@transaction.atomic
def mark_scheduled_task_failure(scheduled_task: ScheduledTask, error_text: str) -> ScheduledTask:
    scheduled_task.failure_count = int(scheduled_task.failure_count or 0) + 1
    scheduled_task.last_error = _trim_text(error_text, MAX_RESULT_SUMMARY_CHARS)
    scheduled_task.save(update_fields=["failure_count", "last_error", "updated_at"])
    return scheduled_task


def compute_next_run_at(local_time_value: time, timezone_name: str, now: datetime | None = None) -> datetime:
    zone = _load_zoneinfo(timezone_name)
    current_time = now or timezone.now()
    current_local = current_time.astimezone(zone)
    candidate = datetime.combine(current_local.date(), local_time_value, tzinfo=zone)
    if candidate <= current_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(dt_timezone.utc)


def execute_scheduled_task(scheduled_task: ScheduledTask) -> str:
    if scheduled_task.task_type == ScheduledTask.TaskType.DAILY_WEATHER_REPORT:
        summary = _execute_daily_weather_report(scheduled_task)
        remember(
            agent=scheduled_task.agent,
            memory_kind=MemoryRecord.MemoryKind.EPISODIC,
            content=(
                f"Scheduled task '{scheduled_task.title or scheduled_task.task_type}' executed on "
                f"{timezone.now().isoformat()} with summary: {summary}"
            ),
            tags=["scheduled-task", "execution", scheduled_task.task_type.replace("_", "-")],
            summary=f"Executed {scheduled_task.title or scheduled_task.task_type}",
            importance=0.35,
            source_kind=SCHEDULED_TASK_EXECUTED_SOURCE_KIND,
            source_ref=str(scheduled_task.id),
            dedupe_key=build_scheduled_task_execution_bucket_key(scheduled_task),
            dedupe_mode="exact",
        )
        return summary
    raise ScheduledTaskConfigurationError(f"Unsupported scheduled task type: {scheduled_task.task_type}")


def build_scheduled_task_execution_bucket_key(scheduled_task: ScheduledTask) -> str:
    return f"{SCHEDULED_TASK_EXEC_BUCKET_PREFIX}:{scheduled_task.id}:{scheduled_task.task_type}"


def _execute_daily_weather_report(scheduled_task: ScheduledTask) -> str:
    payload = dict(scheduled_task.execution_payload or {})
    query = str(payload.get("query") or "").strip()
    location = str(payload.get("location") or "").strip()
    source_domain = str(payload.get("source_domain") or "weather.com").strip() or "weather.com"
    if not query:
        if not location:
            raise ScheduledTaskConfigurationError("daily_weather_report requires execution_payload.location or execution_payload.query")
        query = f"site:{source_domain} {location} daily and weekly weather forecast"

    search_result = _call_toolrunner_tool(
        tool_name="web_search",
        args={
            "query": query,
            "max_results": int(payload.get("max_results") or DEFAULT_WEATHER_MAX_RESULTS),
        },
        workspace_id=str(scheduled_task.workspace_id),
    )
    results = list(search_result.get("results") or [])
    if not results:
        raise ScheduledTaskExecutionError(f"No search results returned for query '{query}'")

    selected = _select_preferred_search_result(results, source_domain)
    selected_url = str(selected.get("url") or "").strip()
    if not selected_url:
        raise ScheduledTaskExecutionError("The selected weather search result did not include a URL")

    fetch_result = _call_toolrunner_tool(
        tool_name="fetch_url",
        args={
            "url": selected_url,
            "extract": "main_text",
            "max_chars": int(payload.get("max_chars") or DEFAULT_WEATHER_MAX_CHARS),
        },
        workspace_id=str(scheduled_task.workspace_id),
    )

    report_body = _build_weather_report_text(
        scheduled_task=scheduled_task,
        query=query,
        selected_result=selected,
        fetch_result=fetch_result,
    )
    _deliver_scheduled_task_message(scheduled_task, report_body)
    return report_body


def _select_preferred_search_result(results: list[dict], source_domain: str) -> dict:
    preferred_domain = source_domain.lower().strip()
    for item in results:
        url = str(item.get("url") or "").lower()
        if preferred_domain and preferred_domain in url:
            return item
    return results[0]


def _build_weather_report_text(*, scheduled_task: ScheduledTask, query: str, selected_result: dict, fetch_result: dict) -> str:
    selected_title = str(selected_result.get("title") or "").strip() or "Weather report"
    selected_url = str(fetch_result.get("final_url") or selected_result.get("url") or "").strip()
    page_title = str(fetch_result.get("title") or "").strip()
    content = str(fetch_result.get("content") or "").strip()
    if not content:
        content = str(selected_result.get("snippet") or "").strip()
    truncated = bool(fetch_result.get("truncated"))

    lines = [f"{scheduled_task.title or 'daily weather report'}"]
    if page_title and page_title.lower() != selected_title.lower():
        lines.append(f"page: {page_title}")
    lines.append(f"source: {selected_title}")
    if selected_url:
        lines.append(f"url: {selected_url}")
    lines.append(f"query: {query}")
    lines.append("")
    lines.append(content or "No readable forecast content was returned.")
    if truncated:
        lines.append("")
        lines.append("Note: fetched content was truncated to keep the report concise.")
    return _trim_text("\n".join(lines).strip(), MAX_RESULT_SUMMARY_CHARS)


def _deliver_scheduled_task_message(scheduled_task: ScheduledTask, text: str) -> None:
    from comms.services.agent_chat_bridge import paired_conversation_for_agent

    conversation = paired_conversation_for_agent(scheduled_task.agent)
    if conversation is None:
        logger.warning(
            "No paired transport conversation found for scheduled task %s agent=%s",
            scheduled_task.id,
            scheduled_task.agent.slug,
        )
        return

    actor_label = (scheduled_task.agent.name or "agent").strip().lower() or "agent"
    rendered = f"<i>{_escape_html(actor_label)}</i>\n{_escape_html(text)}"
    send_conversation_message(
        conversation,
        rendered,
        actor_label=actor_label,
        author_type="system",
        control_direction="system",
        mirror_to_control=False,
        parse_mode="HTML",
    )


def _call_toolrunner_tool(*, tool_name: str, args: dict, workspace_id: str) -> dict:
    repo_root = str(Path(settings.BASE_DIR).resolve().parent)
    sandbox_root = Path(getattr(settings, "TOOLRUNNER_SANDBOX_ROOT", _DEFAULT_TOOLRUNNER_SANDBOX_ROOT)).resolve()
    payload = {
        "request_id": str(uuid.uuid4()),
        "workspace_id": workspace_id,
        "run_id": f"scheduled-task:{uuid.uuid4()}",
        "tool_name": tool_name,
        "args": args,
        "policy": {
            "risk_level": "safe",
            "tool_definition_id": "scheduled-task",
            "requires_approval": False,
            "allow_write": False,
            "allowed_roots": [],
            "repo_root": repo_root,
            "tmp_root": str(sandbox_root.parent),
        },
        "limits": {
            "timeout_s": getattr(settings, "TOOLRUNNER_TIMEOUT", 30),
            "max_output_bytes": getattr(settings, "TOOLRUNNER_OUTPUT_LIMIT", 4096),
        },
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time_module.time()))
    signature = _sign_payload(body, timestamp)
    headers = {
        "X-AM-Timestamp": timestamp,
        "X-AM-Signature": signature,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=float(getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", 45))) as client:
        response = client.post(settings.TOOLRUNNER_URL, content=body, headers=headers)
    response.raise_for_status()
    data = response.json()
    if str(data.get("status") or "") != "COMPLETED":
        raise ScheduledTaskExecutionError(
            f"Toolrunner returned status {data.get('status')} for {tool_name}: {data.get('stderr') or data.get('result') or ''}"
        )
    return dict(data.get("result") or {})


def _sign_payload(body: bytes, timestamp: str) -> str:
    key = settings.TOOLRUNNER_SECRET.encode("utf-8")
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(key, message, sha256).hexdigest()


def _normalize_local_time(value: str | time) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    candidate = str(value or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(candidate, fmt)
            return parsed.time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ScheduledTaskConfigurationError(f"Invalid local time '{value}'. Expected HH:MM or HH:MM:SS.")


def _normalize_timezone_name(value: str) -> str:
    candidate = str(value or "").strip() or "UTC"
    _load_zoneinfo(candidate)
    return candidate


def _load_zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "UTC"))
    except Exception as exc:  # noqa: BLE001
        raise ScheduledTaskConfigurationError(f"Unknown timezone '{timezone_name}'") from exc


def _build_schedule_memory_content(*, task_type: str, timezone_name: str, local_time_value: time, execution_payload: dict) -> str:
    payload_bits = []
    if execution_payload.get("location"):
        payload_bits.append(f"location={execution_payload['location']}")
    if execution_payload.get("query"):
        payload_bits.append(f"query={execution_payload['query']}")
    if execution_payload.get("source_domain"):
        payload_bits.append(f"source_domain={execution_payload['source_domain']}")
    payload_suffix = f" ({', '.join(payload_bits)})" if payload_bits else ""
    return (
        f"Scheduled recurring task '{task_type}' for {local_time_value.isoformat(timespec='minutes')} {timezone_name}"
        f"{payload_suffix}."
    )


def _default_task_title(task_type: str, execution_payload: dict) -> str:
    if task_type == ScheduledTask.TaskType.DAILY_WEATHER_REPORT:
        location = str(execution_payload.get("location") or "").strip()
        if location:
            return f"daily weather report for {location}".strip()
        return "daily weather report"
    return task_type.replace("_", " ")


def _trim_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return suffix[:limit]
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


def _escape_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
