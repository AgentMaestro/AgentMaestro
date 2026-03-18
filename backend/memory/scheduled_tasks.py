from __future__ import annotations

import hmac
import json
import logging
import time as time_module
import uuid
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from comms.services.outbound import send_conversation_message
from memory.models import MemoryRecord, RecurrenceRule, ScheduledTask
from memory.recurrence import describe_recurrence_rule, get_next_occurrence, normalize_recurrence_rule_data
from memory.services import remember
from runs.models import AgentRun
from runs.services.headless import mark_headless_run_stale
from runs.services.state import FINAL_RUN_STATUSES

logger = logging.getLogger(__name__)

MAX_RESULT_SUMMARY_CHARS = 2000
DEFAULT_SCHEDULED_TASK_LIMIT = 10
DEFAULT_WEATHER_MAX_RESULTS = 5
DEFAULT_WEATHER_MAX_CHARS = 4000
_DEFAULT_TOOLRUNNER_SANDBOX_ROOT = Path("C:/tmp/agentmaestro/sandbox")

SCHEDULED_TASK_CREATED_SOURCE_KIND = "scheduled_task_created"
SCHEDULED_TASK_EXECUTED_SOURCE_KIND = "scheduled_task_executed"
SCHEDULED_TASK_EXEC_BUCKET_PREFIX = "scheduled-task-exec-bucket"
STALE_HEADLESS_RUN_REASON = "stale_headless_run_timeout"


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
    local_time_value: str | time | None = None,
    timezone_name: str | None = None,
    title: str = "",
    execution_payload: dict | None = None,
    delivery_target: str = ScheduledTask.DeliveryTarget.PAIRED_TRANSPORT,
    execution_mode: str = ScheduledTask.ExecutionMode.DETERMINISTIC,
    source_memory: MemoryRecord | None = None,
    enabled: bool = True,
    recurrence_rule: RecurrenceRule | None = None,
    recurrence_config: dict | None = None,
) -> ScheduledTask:
    scheduled_task_id = uuid.uuid4()
    normalized_payload = dict(execution_payload or {})
    normalized_execution_mode = _normalize_execution_mode(execution_mode, task_type=task_type)
    rule = _resolve_recurrence_rule(
        recurrence_rule=recurrence_rule,
        recurrence_config=recurrence_config,
        local_time_value=local_time_value,
        timezone_name=timezone_name,
        title=title,
        execution_payload=normalized_payload,
    )
    transitional_timezone, transitional_local_time, schedule_kind = _derive_task_schedule_fields(rule)
    next_run_at = compute_next_run_at(rule)
    if next_run_at is None and enabled:
        raise ScheduledTaskConfigurationError("The supplied recurrence rule does not produce any future occurrences.")

    if source_memory is None:
        source_memory = remember(
            agent=agent,
            memory_kind=MemoryRecord.MemoryKind.EPISODIC,
            content=_build_schedule_memory_content(
                task_type=task_type,
                recurrence_rule=rule,
                execution_payload=normalized_payload,
                execution_mode=normalized_execution_mode,
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
        recurrence_rule=rule,
        source_memory=source_memory,
        title=title or _default_task_title(task_type, normalized_payload),
        task_type=task_type,
        schedule_kind=schedule_kind,
        delivery_target=delivery_target,
        execution_mode=normalized_execution_mode,
        timezone=transitional_timezone,
        local_time=transitional_local_time,
        next_run_at=next_run_at or timezone.now(),
        enabled=enabled and next_run_at is not None,
        execution_payload=normalized_payload,
    )



def list_scheduled_tasks(*, agent=None, workspace=None, owner=None, enabled_only: bool = False, limit: int = 20):
    queryset = ScheduledTask.objects.select_related(
        "agent",
        "owner",
        "workspace",
        "source_memory",
        "last_run",
        "active_run",
        "recurrence_rule",
    )
    if agent is not None:
        queryset = queryset.filter(agent=agent)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    return list(queryset.order_by("next_run_at", "created_at")[: max(1, min(int(limit or 20), 50))])



def cleanup_scheduled_task_active_runs(*, now: datetime | None = None, limit: int = DEFAULT_SCHEDULED_TASK_LIMIT) -> dict[str, int]:
    current_time = now or timezone.now()
    stale_minutes = int(getattr(settings, "HEADLESS_RUN_STALE_TIMEOUT_MINUTES", 30))
    stale_before = current_time - timedelta(minutes=max(stale_minutes, 1))
    terminal_cleared = 0
    stale_runs: list[tuple[str, str]] = []

    with transaction.atomic():
        active_tasks = list(
            ScheduledTask.objects.select_for_update(skip_locked=True)
            .filter(active_run__isnull=False)
            .select_related("agent", "owner", "workspace", "recurrence_rule")
            .order_by("updated_at")[: max(1, min(int(limit or DEFAULT_SCHEDULED_TASK_LIMIT), 100))]
        )
        for scheduled_task in active_tasks:
            active_run = scheduled_task.active_run
            if active_run is None:
                continue
            if active_run.status in FINAL_RUN_STATUSES:
                scheduled_task.last_run = active_run
                scheduled_task.active_run = None
                scheduled_task.save(update_fields=["last_run", "active_run", "updated_at"])
                terminal_cleared += 1
                continue
            if active_run.status == active_run.Status.WAITING_FOR_APPROVAL:
                continue
            if _active_run_touch(active_run) <= stale_before:
                stale_runs.append((str(scheduled_task.id), str(active_run.id)))

    stale_cleared = 0
    for scheduled_task_id, run_id in stale_runs:
        scheduled_task = ScheduledTask.objects.select_related("recurrence_rule").filter(id=scheduled_task_id).first()
        if scheduled_task is None:
            continue
        mark_headless_run_stale(run_id, scheduled_task=scheduled_task, reason=STALE_HEADLESS_RUN_REASON)
        stale_cleared += 1

    return {"terminal_cleared": terminal_cleared, "stale_cleared": stale_cleared}


@transaction.atomic
def claim_due_scheduled_tasks(*, now: datetime | None = None, limit: int = DEFAULT_SCHEDULED_TASK_LIMIT) -> list[ScheduledTask]:
    current_time = now or timezone.now()
    cleanup_scheduled_task_active_runs(now=current_time, limit=limit)
    effective_limit = max(1, min(int(limit or DEFAULT_SCHEDULED_TASK_LIMIT), 50))
    candidate_limit = max(effective_limit * 3, effective_limit)
    candidate_limit = min(candidate_limit, 100)

    candidate_tasks = list(
        ScheduledTask.objects.select_for_update(skip_locked=True)
        .filter(enabled=True, next_run_at__lte=current_time)
        .select_related("agent", "owner", "workspace", "recurrence_rule")
        .order_by("next_run_at", "created_at")[:candidate_limit]
    )

    active_run_ids = [task.active_run_id for task in candidate_tasks if task.active_run_id]
    active_status_by_id = {
        str(run_id): status
        for run_id, status in AgentRun.objects.filter(id__in=active_run_ids).values_list("id", "status")
    }

    due_tasks: list[ScheduledTask] = []
    for scheduled_task in candidate_tasks:
        active_run_id = scheduled_task.active_run_id
        active_status = active_status_by_id.get(str(active_run_id)) if active_run_id else None
        if active_run_id and active_status not in FINAL_RUN_STATUSES:
            continue
        if active_run_id and active_status in FINAL_RUN_STATUSES:
            scheduled_task.last_run_id = active_run_id
            scheduled_task.active_run = None
        scheduled_task.last_run_at = current_time
        next_run_at = compute_next_run_at(scheduled_task.recurrence_rule, now=current_time + timedelta(seconds=1))
        scheduled_task.next_run_at = next_run_at or current_time
        if next_run_at is None:
            scheduled_task.enabled = False
        scheduled_task.save(update_fields=["active_run", "last_run", "last_run_at", "next_run_at", "enabled", "updated_at"])
        due_tasks.append(scheduled_task)
        if len(due_tasks) >= effective_limit:
            break
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



def compute_next_run_at(recurrence_rule: RecurrenceRule, now: datetime | None = None) -> datetime | None:
    return get_next_occurrence(recurrence_rule, now or timezone.now())



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



def deliver_scheduled_task_message(scheduled_task: ScheduledTask, text: str) -> None:
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



def _resolve_recurrence_rule(
    *,
    recurrence_rule: RecurrenceRule | None,
    recurrence_config: dict | None,
    local_time_value: str | time | None,
    timezone_name: str | None,
    title: str,
    execution_payload: dict | None,
) -> RecurrenceRule:
    if recurrence_rule is not None and recurrence_config is not None:
        raise ScheduledTaskConfigurationError("Provide either recurrence_rule or recurrence_config, not both.")
    if recurrence_rule is not None:
        if recurrence_rule.pk is None:
            recurrence_rule.full_clean()
            recurrence_rule.save()
        return recurrence_rule

    if recurrence_config is None:
        if local_time_value is None or timezone_name is None:
            raise ScheduledTaskConfigurationError("timezone and local_time are required when no recurrence rule is supplied.")
        normalized_timezone = _normalize_timezone_name(timezone_name)
        recurrence_config = {
            "name": title,
            "timezone": normalized_timezone,
            "frequency": RecurrenceRule.Frequency.DAILY,
            "interval": 1,
            "local_time": _normalize_local_time(local_time_value),
            "start_date": _current_local_date(normalized_timezone),
            "is_active": True,
        }
        recurrence_config = _merge_payload_recurrence_hints(recurrence_config, execution_payload)
    else:
        recurrence_config = dict(recurrence_config)
        recurrence_config.setdefault("name", title)
        if timezone_name and "timezone" not in recurrence_config:
            recurrence_config["timezone"] = _normalize_timezone_name(timezone_name)
        if local_time_value is not None and "local_time" not in recurrence_config and "run_minute" not in recurrence_config:
            recurrence_config["local_time"] = _normalize_local_time(local_time_value)
        if recurrence_config.get("start_date") in (None, "") and recurrence_config.get("timezone"):
            recurrence_config["start_date"] = _current_local_date(str(recurrence_config["timezone"]))

    try:
        normalized = normalize_recurrence_rule_data(recurrence_config)
    except ValidationError as exc:
        raise ScheduledTaskConfigurationError(exc.message_dict if hasattr(exc, "message_dict") else str(exc)) from exc
    rule = RecurrenceRule(**normalized)
    rule.full_clean()
    rule.save()
    return rule



def _derive_task_schedule_fields(recurrence_rule: RecurrenceRule) -> tuple[str, time, str]:
    fallback_time = recurrence_rule.local_time
    if fallback_time is None and recurrence_rule.window_start_time is not None:
        fallback_time = recurrence_rule.window_start_time
    if fallback_time is None:
        fallback_time = time(hour=0, minute=int(recurrence_rule.run_minute or 0))
    schedule_kind = (
        ScheduledTask.ScheduleKind.DAILY_TIME
        if recurrence_rule.frequency == RecurrenceRule.Frequency.DAILY and not recurrence_rule.by_weekday
        else ScheduledTask.ScheduleKind.RECURRENCE_RULE
    )
    return recurrence_rule.timezone, fallback_time, schedule_kind


def _merge_payload_recurrence_hints(base_config: dict, execution_payload: dict | None) -> dict:
    payload = dict(execution_payload or {})
    focus_days = _normalize_focus_days(payload.get("focus_days"))
    if focus_days and not base_config.get("by_weekday"):
        base_config["frequency"] = RecurrenceRule.Frequency.WEEKLY
        base_config["by_weekday"] = focus_days
    return base_config



def _normalize_focus_days(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        return []
    day_map = {
        "mon": "mon",
        "monday": "mon",
        "tue": "tue",
        "tues": "tue",
        "tuesday": "tue",
        "wed": "wed",
        "wednesday": "wed",
        "thu": "thu",
        "thur": "thu",
        "thurs": "thu",
        "thursday": "thu",
        "fri": "fri",
        "friday": "fri",
        "sat": "sat",
        "saturday": "sat",
        "sun": "sun",
        "sunday": "sun",
    }
    normalized: list[str] = []
    for item in value:
        candidate = day_map.get(str(item or "").strip().lower())
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized



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



def _deliver_scheduled_task_message(scheduled_task: ScheduledTask, text: str) -> None:
    deliver_scheduled_task_message(scheduled_task, text)



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



def _task_type_requires_headless_run(task_type: str) -> bool:
    return str(task_type or "").strip().lower() in {
        ScheduledTask.TaskType.OTHER_DAILY_TASK,
        ScheduledTask.TaskType.DAILY_EMAIL_CHECK,
    }



def ensure_scheduled_task_execution_mode(scheduled_task: ScheduledTask) -> tuple[str, bool]:
    normalized = _normalize_execution_mode(
        scheduled_task.execution_mode,
        task_type=scheduled_task.task_type,
    )
    if normalized == scheduled_task.execution_mode:
        return normalized, False
    scheduled_task.execution_mode = normalized
    scheduled_task.save(update_fields=["execution_mode", "updated_at"])
    logger.warning(
        "Corrected scheduled task execution mode task=%s type=%s execution_mode=%s",
        scheduled_task.id,
        scheduled_task.task_type,
        normalized,
    )
    return normalized, True



def _normalize_execution_mode(value: str, *, task_type: str | None = None) -> str:
    candidate = str(value or ScheduledTask.ExecutionMode.DETERMINISTIC).strip().lower()
    valid = {choice for choice, _label in ScheduledTask.ExecutionMode.choices}
    if candidate not in valid:
        raise ScheduledTaskConfigurationError(f"Unsupported execution mode '{value}'.")
    if task_type and _task_type_requires_headless_run(task_type) and candidate != ScheduledTask.ExecutionMode.HEADLESS_RUN:
        logger.warning(
            "Coercing scheduled task execution mode to headless_run for task_type=%s requested_mode=%s",
            task_type,
            candidate,
        )
        return ScheduledTask.ExecutionMode.HEADLESS_RUN
    return candidate



def _load_zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "UTC"))
    except Exception as exc:  # noqa: BLE001
        raise ScheduledTaskConfigurationError(f"Unknown timezone '{timezone_name}'") from exc



def _current_local_date(timezone_name: str) -> date:
    return timezone.now().astimezone(_load_zoneinfo(timezone_name)).date()



def _build_schedule_memory_content(
    *,
    task_type: str,
    recurrence_rule: RecurrenceRule,
    execution_payload: dict,
    execution_mode: str,
) -> str:
    payload_bits = []
    if execution_payload.get("location"):
        payload_bits.append(f"location={execution_payload['location']}")
    if execution_payload.get("query"):
        payload_bits.append(f"query={execution_payload['query']}")
    if execution_payload.get("source_domain"):
        payload_bits.append(f"source_domain={execution_payload['source_domain']}")
    payload_suffix = f" ({', '.join(payload_bits)})" if payload_bits else ""
    return (
        f"Scheduled recurring task '{task_type}' using recurrence '{describe_recurrence_rule(recurrence_rule)}' "
        f"with execution_mode={execution_mode}{payload_suffix}."
    )



def _default_task_title(task_type: str, execution_payload: dict) -> str:
    if task_type == ScheduledTask.TaskType.DAILY_WEATHER_REPORT:
        location = str(execution_payload.get("location") or "").strip()
        if location:
            return f"daily weather report for {location}".strip()
        return "daily weather report"
    return task_type.replace("_", " ")



def _active_run_touch(active_run) -> datetime:
    return active_run.updated_at or active_run.locked_at or active_run.started_at or active_run.created_at



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
