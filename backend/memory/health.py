from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from memory.models import MemoryHealthSnapshot, MemoryRecord, ScheduledTask
from memory.retention import (
    DEFAULT_PROTECTED_IMPORTANCE_THRESHOLD,
    get_memory_retention_health,
    get_retention_cutoff,
)
from memory.scheduled_tasks import (
    SCHEDULED_TASK_CREATED_SOURCE_KIND,
    SCHEDULED_TASK_EXECUTED_SOURCE_KIND,
)


def build_memory_health_report(
    *,
    now: datetime | None = None,
    compare_days: int = 30,
    save_snapshot: bool = True,
) -> dict[str, object]:
    current_time = now or timezone.now()
    retention_days = int(getattr(settings, "MEMORY_RETENTION_DAYS", 30))
    protected_threshold = _protected_threshold()
    retention_health = get_memory_retention_health(now=current_time, retention_days=retention_days)
    comparison_cutoff = current_time - timedelta(days=max(int(compare_days or 30), 1))

    total_records = MemoryRecord.objects.count()
    pinned_records = MemoryRecord.objects.filter(pinned=True).count()
    high_importance_records = MemoryRecord.objects.filter(importance__gte=protected_threshold).count()
    expired_records = MemoryRecord.objects.filter(expires_at__isnull=False, expires_at__lte=current_time).count()
    active_records = total_records - expired_records
    created_last_compare_window = MemoryRecord.objects.filter(created_at__gte=comparison_cutoff).count()
    updated_last_compare_window = MemoryRecord.objects.filter(updated_at__gte=comparison_cutoff).count()

    memory_summary = {
        "total_records": total_records,
        "active_records": active_records,
        "expired_records": expired_records,
        "pinned_records": pinned_records,
        "high_importance_records": high_importance_records,
        "with_dedupe_key": MemoryRecord.objects.exclude(dedupe_key="").count(),
        "with_source_kind": MemoryRecord.objects.exclude(source_kind="").count(),
        "created_last_compare_window": created_last_compare_window,
        "updated_last_compare_window": updated_last_compare_window,
        "by_kind": _counts_by_choice(
            MemoryRecord.MemoryKind.choices,
            MemoryRecord.objects.values("memory_kind").annotate(total=Count("id")),
            "memory_kind",
        ),
        "by_scope_type": _counts_by_choice(
            MemoryRecord.ScopeType.choices,
            MemoryRecord.objects.values("scope_type").annotate(total=Count("id")),
            "scope_type",
        ),
        "by_source_kind": {
            row["source_kind"] or "": int(row["total"])
            for row in MemoryRecord.objects.values("source_kind").annotate(total=Count("id")).order_by("source_kind")
            if row["source_kind"]
        },
    }

    scheduled_task_summary = {
        "total": ScheduledTask.objects.count(),
        "enabled": ScheduledTask.objects.filter(enabled=True).count(),
        "disabled": ScheduledTask.objects.filter(enabled=False).count(),
        "executed_at_least_once": ScheduledTask.objects.filter(last_run_at__isnull=False).count(),
        "never_run": ScheduledTask.objects.filter(last_run_at__isnull=True).count(),
        "failed": ScheduledTask.objects.filter(failure_count__gt=0).count(),
        "by_type": _counts_by_choice(
            ScheduledTask.TaskType.choices,
            ScheduledTask.objects.values("task_type").annotate(total=Count("id")),
            "task_type",
        ),
        "memory_records": {
            "scheduled_task_created": MemoryRecord.objects.filter(source_kind=SCHEDULED_TASK_CREATED_SOURCE_KIND).count(),
            "scheduled_task_executed": MemoryRecord.objects.filter(source_kind=SCHEDULED_TASK_EXECUTED_SOURCE_KIND).count(),
            "distilled_memory": MemoryRecord.objects.filter(source_kind="distilled_memory").count(),
        },
    }

    trend = _build_growth_trend(
        current_time=current_time,
        compare_days=compare_days,
        total_records=total_records,
        pinned_records=pinned_records,
        high_importance_records=high_importance_records,
        retention_candidate_records=int(retention_health["eligible_records"]),
        scheduled_tasks_total=scheduled_task_summary["total"],
    )

    report = {
        "generated_at": current_time.isoformat(),
        "retention_days": retention_days,
        "compare_days": int(compare_days or 30),
        "memory": memory_summary,
        "scheduled_tasks": scheduled_task_summary,
        "retention": retention_health,
        "trend": trend,
    }

    if save_snapshot:
        snapshot = MemoryHealthSnapshot.objects.create(
            retention_days=retention_days,
            compare_days=int(compare_days or 30),
            total_records=total_records,
            pinned_records=pinned_records,
            high_importance_records=high_importance_records,
            retention_candidate_records=int(retention_health["eligible_records"]),
            scheduled_tasks_total=scheduled_task_summary["total"],
            report_json=report,
        )
        report["snapshot"] = {
            "id": str(snapshot.id),
            "captured_at": snapshot.created_at.isoformat(),
        }
    else:
        report["snapshot"] = None

    return report


def _build_growth_trend(
    *,
    current_time: datetime,
    compare_days: int,
    total_records: int,
    pinned_records: int,
    high_importance_records: int,
    retention_candidate_records: int,
    scheduled_tasks_total: int,
) -> dict[str, object]:
    compare_window_days = max(int(compare_days or 30), 1)
    baseline_cutoff = current_time - timedelta(days=compare_window_days)
    baseline = MemoryHealthSnapshot.objects.filter(created_at__lte=baseline_cutoff).order_by("-created_at").first()
    if baseline is None:
        return {
            "compare_days": compare_window_days,
            "baseline_available": False,
            "baseline_cutoff": baseline_cutoff.isoformat(),
            "baseline_snapshot_id": None,
            "baseline_captured_at": None,
            "total_records_then": None,
            "total_records_now": total_records,
            "delta_total_records": None,
            "delta_pinned_records": None,
            "delta_high_importance_records": None,
            "delta_retention_candidate_records": None,
            "delta_scheduled_tasks_total": None,
        }
    return {
        "compare_days": compare_window_days,
        "baseline_available": True,
        "baseline_cutoff": baseline_cutoff.isoformat(),
        "baseline_snapshot_id": str(baseline.id),
        "baseline_captured_at": baseline.created_at.isoformat(),
        "total_records_then": int(baseline.total_records),
        "total_records_now": total_records,
        "delta_total_records": total_records - int(baseline.total_records),
        "delta_pinned_records": pinned_records - int(baseline.pinned_records),
        "delta_high_importance_records": high_importance_records - int(baseline.high_importance_records),
        "delta_retention_candidate_records": retention_candidate_records - int(baseline.retention_candidate_records),
        "delta_scheduled_tasks_total": scheduled_tasks_total - int(baseline.scheduled_tasks_total),
    }


def _counts_by_choice(choices: Iterable[tuple[str, str]], rows, key_name: str) -> dict[str, int]:
    counts = {value: 0 for value, _label in choices}
    for row in rows:
        counts[str(row[key_name])] = int(row["total"])
    return counts


def _protected_threshold():
    from memory.retention import _decimal_setting

    return _decimal_setting(
        "MEMORY_RETENTION_PROTECTED_IMPORTANCE_THRESHOLD",
        DEFAULT_PROTECTED_IMPORTANCE_THRESHOLD,
    )
