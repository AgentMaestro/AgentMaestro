from __future__ import annotations

from copy import deepcopy
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from logging_utils import get_app_logger

from core.services.timezones import get_local_timezone_name
from memory.models import MemoryRecord, RecurrenceRule, ScheduledTask
from memory.recurrence import describe_recurrence_rule, get_next_occurrence, normalize_recurrence_rule_data
from memory.services import remember
from runs.models import AgentRun
from runs.services.headless import mark_headless_run_stale
from runs.services.state import FINAL_RUN_STATUSES

logger = get_app_logger(__name__)

MAX_RESULT_SUMMARY_CHARS = 2000
DEFAULT_SCHEDULED_TASK_LIMIT = 10

SCHEDULED_TASK_CREATED_SOURCE_KIND = "scheduled_task_created"
SCHEDULED_TASK_EXECUTED_SOURCE_KIND = "scheduled_task_executed"
SCHEDULED_TASK_EXEC_BUCKET_PREFIX = "scheduled-task-exec-bucket"
STALE_HEADLESS_RUN_REASON = "stale_headless_run_timeout"


class ScheduledTaskError(RuntimeError):
    pass


class ScheduledTaskConfigurationError(ScheduledTaskError):
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
    execution_mode: str = ScheduledTask.ExecutionMode.HEADLESS_RUN,
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

    scheduled_task = ScheduledTask.objects.create(
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

    # If the scheduled task's payload requests a remote mutation (for example
    # google mailbox mutations like trash/delete), create an ApprovalRequest now
    # so the regular approval flow can approve the mutation before the task's
    # first run. This prevents the scheduler from creating a blocking request
    # at run time and allows operators to choose approve-once / timed / future.
    try:
        payload = dict(normalized_payload or {})
        action = str(payload.get('action') or '').strip().lower()
        integration = str(payload.get('integration_kind') or '').strip().lower()
        # Treat 'trash' and 'delete' and any explicit google integration as write ops
        if action in ('trash', 'delete') or integration == 'google':
            ApprovalRequest = apps.get_model('control', 'ApprovalRequest')
            # Build a concise payload preview and constraints for the approval UI
            preview = {
                'action': action,
                'domains': payload.get('domains', []),
                'account_scope': payload.get('account_scope'),
            }
            constraints = {
                'resource_kind': payload.get('resource_kind', 'gmail'),
                'action_kind': 'delete' if action in ('trash', 'delete') else action,
                'account_scope': payload.get('account_scope', 'primary'),
                'domains': payload.get('domains', []),
            }
            summary = payload.get('objective') or f"Scheduled task '{scheduled_task.title}' requests {action} on {constraints.get('account_scope')} accounts"
            # requested_by expects an Operator; owner is typically the operator/user who created the task
            requested_by = None
            try:
                # If owner is an Operator instance, use it. Otherwise leave null and let UI assign.
                from control.models import Operator

                if isinstance(owner, Operator):
                    requested_by = owner
            except Exception:
                requested_by = None

            ApprovalRequest.objects.create(
                run_id=None,
                risk_level='external_write',
                tool_name='google_bridge',
                summary=summary,
                payload_preview=preview,
                constraints=constraints,
                status=ApprovalRequest.STATUS_PENDING,
                requested_by=requested_by,
            )
    except Exception:
        # Do not fail scheduled task creation if approval bookkeeping fails;
        # log and continue. The normal runtime approval path will still work.
        logger.exception('Failed to create initial ApprovalRequest for scheduled task %s', scheduled_task_id)

    return scheduled_task


@transaction.atomic
def update_scheduled_task(
    scheduled_task: ScheduledTask,
    *,
    title: str | None = None,
    enabled: bool | None = None,
    execution_payload: dict | None = None,
    recurrence_config: dict | None = None,
    local_time_value: str | time | None = None,
    timezone_name: str | None = None,
    delivery_target: str | None = None,
) -> ScheduledTask:
    task = (
        ScheduledTask.objects.select_for_update()
        .select_related("recurrence_rule", "workspace", "agent", "owner")
        .get(id=scheduled_task.id)
    )
    normalized_payload = dict(task.execution_payload or {})
    if execution_payload is not None:
        normalized_payload = dict(execution_payload or {})

    recurrence_changed = recurrence_config is not None or local_time_value is not None or timezone_name is not None
    if recurrence_changed:
        new_rule = _resolve_recurrence_rule(
            recurrence_rule=None,
            recurrence_config=recurrence_config,
            local_time_value=local_time_value,
            timezone_name=timezone_name,
            title=title if title is not None else task.title,
            execution_payload=normalized_payload,
        )
        task.recurrence_rule = new_rule

    if title is not None:
        task.title = title.strip()
    task.execution_payload = normalized_payload
    if delivery_target is not None:
        task.delivery_target = delivery_target
    if enabled is not None:
        task.enabled = bool(enabled)

    if recurrence_changed or enabled is True:
        next_run_at = compute_next_run_at(task.recurrence_rule, now=timezone.now())
        if next_run_at is None:
            raise ScheduledTaskConfigurationError("The supplied recurrence rule does not produce any future occurrences.")
        task.next_run_at = next_run_at

    task.full_clean()
    task.save(
        update_fields=[
            "recurrence_rule",
            "title",
            "execution_payload",
            "delivery_target",
            "enabled",
            "timezone",
            "local_time",
            "schedule_kind",
            "next_run_at",
            "updated_at",
        ]
    )
    return task


@transaction.atomic
def disable_scheduled_task(scheduled_task: ScheduledTask) -> ScheduledTask:
    task = ScheduledTask.objects.select_for_update().get(id=scheduled_task.id)
    task.enabled = False
    task.save(update_fields=["enabled", "updated_at"])
    return task


@transaction.atomic
def enable_scheduled_task(scheduled_task: ScheduledTask) -> ScheduledTask:
    task = ScheduledTask.objects.select_for_update().select_related("recurrence_rule").get(id=scheduled_task.id)
    next_run_at = compute_next_run_at(task.recurrence_rule, now=timezone.now())
    if next_run_at is None:
        raise ScheduledTaskConfigurationError("The scheduled task recurrence does not produce any future occurrences.")
    task.enabled = True
    task.next_run_at = next_run_at
    task.full_clean()
    task.save(update_fields=["enabled", "next_run_at", "timezone", "local_time", "schedule_kind", "updated_at"])
    return task


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
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    return list(queryset.order_by("next_run_at", "created_at")[: max(1, min(int(limit or 20), 50))])


def get_scheduled_task(*, agent=None, workspace=None, owner=None, scheduled_task_id: str):
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
    return queryset.filter(id=scheduled_task_id).first()



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
    from runs.services.headless import execute_headless_run
    from runs.services.headless import launch_scheduled_task_run
    from runs.tasks import execute_headless_run_task

    scheduled_task, run, did_launch = launch_scheduled_task_run(str(scheduled_task.id))
    if not did_launch:
        return f"Scheduled task '{scheduled_task.title or scheduled_task.task_type}' already has an active headless run."
    if run.status == AgentRun.Status.WAITING_FOR_APPROVAL:
        return f"Scheduled task '{scheduled_task.title or scheduled_task.task_type}' is waiting for approval."

    # Keep the direct helper usable for legacy call sites, but execute the same
    # headless pipeline that the scheduler now uses everywhere.
    if run.status == AgentRun.Status.PENDING:
        execute_headless_run_task.delay(str(run.id))
    else:
        execute_headless_run(str(run.id))
    return f"Scheduled task '{scheduled_task.title or scheduled_task.task_type}' launched as headless run {run.id}."


def run_scheduled_task_now(scheduled_task_id: str) -> dict[str, object]:
    from runs.models import AgentRun
    from runs.tasks import execute_headless_run_task

    scheduled_task, run, did_launch = launch_scheduled_task_run(str(scheduled_task_id))
    waiting_for_approval = run.status == AgentRun.Status.WAITING_FOR_APPROVAL
    result = {
        "scheduled_task_id": str(scheduled_task.id),
        "title": scheduled_task.title,
        "task_type": scheduled_task.task_type,
        "execution_mode": scheduled_task.execution_mode,
        "run_id": str(run.id),
        "active_run_id": str(scheduled_task.active_run_id or ""),
        "launched": did_launch,
        "queued": False,
        "waiting_for_approval": waiting_for_approval,
        "status": "already_running" if not did_launch else ("awaiting_approval" if waiting_for_approval else "launched"),
    }
    if not did_launch or waiting_for_approval:
        return result
    execute_headless_run_task.delay(str(run.id))
    result["queued"] = True
    return result



def build_scheduled_task_execution_bucket_key(scheduled_task: ScheduledTask) -> str:
    return f"{SCHEDULED_TASK_EXEC_BUCKET_PREFIX}:{scheduled_task.id}:{scheduled_task.task_type}"


def serialize_scheduled_task(task: ScheduledTask, *, include_execution_payload: bool = False) -> dict[str, object]:
    payload = {
        "scheduled_task_id": str(task.id),
        "recurrence_rule_id": str(task.recurrence_rule_id),
        "title": task.title,
        "task_type": task.task_type,
        "schedule_kind": task.schedule_kind,
        "execution_mode": task.execution_mode,
        "timezone": task.timezone,
        "local_time": task.local_time.isoformat(timespec="minutes"),
        "recurrence_frequency": task.recurrence_rule.frequency,
        "recurrence_summary": task.recurrence_summary,
        "next_run_at": task.next_run_at.isoformat(),
        "enabled": task.enabled,
        "failure_count": task.failure_count,
        "last_run_id": str(task.last_run_id or ""),
        "active_run_id": str(task.active_run_id or ""),
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else "",
        "last_success_at": task.last_success_at.isoformat() if task.last_success_at else "",
        "last_result_summary": task.last_result_summary,
        "last_error": task.last_error,
    }
    if include_execution_payload:
        payload["execution_payload"] = deepcopy(task.execution_payload or {})
    return payload



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

    timezone_name = timezone_name or get_local_timezone_name()
    if recurrence_config is None:
        if local_time_value is None:
            raise ScheduledTaskConfigurationError("local_time is required when no recurrence rule is supplied.")
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
        recurrence_config.setdefault("timezone", timezone_name)
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



def _normalize_execution_mode(value: str, *, task_type: str | None = None) -> str:
    candidate = str(value or ScheduledTask.ExecutionMode.HEADLESS_RUN).strip().lower()
    if candidate and candidate != ScheduledTask.ExecutionMode.HEADLESS_RUN:
        logger.warning(
            "Ignoring legacy scheduled task execution mode requested_mode=%s task_type=%s",
            candidate,
            task_type,
        )
    return ScheduledTask.ExecutionMode.HEADLESS_RUN



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
    if execution_payload.get("integration_kind"):
        payload_bits.append(f"integration_kind={execution_payload['integration_kind']}")
    if execution_payload.get("resource_kind"):
        payload_bits.append(f"resource_kind={execution_payload['resource_kind']}")
    if execution_payload.get("action_kind"):
        payload_bits.append(f"action_kind={execution_payload['action_kind']}")
    if execution_payload.get("operation"):
        payload_bits.append(f"operation={execution_payload['operation']}")
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
    label = task_type.replace("_", " ").strip() or "scheduled task"
    location = str(execution_payload.get("location") or "").strip()
    if location:
        return f"{label} for {location}"
    return label



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
