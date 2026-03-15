from decimal import Decimal
from datetime import timedelta

import pytest
from django.utils import timezone

from memory.models import MemoryRecord
from memory.retention import DISTILLED_SOURCE_KIND, run_memory_retention
from memory.services import remember


pytestmark = pytest.mark.django_db


def _age_records(*records: MemoryRecord, days: int = 40):
    old_time = timezone.now() - timedelta(days=days)
    MemoryRecord.objects.filter(id__in=[record.id for record in records]).update(
        created_at=old_time,
        updated_at=old_time,
        last_accessed_at=old_time,
    )
    for record in records:
        record.refresh_from_db()


def test_run_memory_retention_dry_run_reports_without_mutating():
    first = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 1 at 08:00 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    second = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 2 at 08:00 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    _age_records(first, second)

    report = run_memory_retention(dry_run=True, retention_days=30, batch_size=50, group_limit=10)

    assert report["dry_run"] is True
    assert report["groups_distilled"] == 1
    assert report["raw_records_purged"] == 2
    assert MemoryRecord.objects.filter(source_kind="scheduled_task_executed").count() == 2
    assert MemoryRecord.objects.filter(source_kind=DISTILLED_SOURCE_KIND).count() == 0


def test_run_memory_retention_distills_and_purges_repetitive_episodic():
    first = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 1 at 08:00 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    second = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 2 at 08:01 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    _age_records(first, second)

    report = run_memory_retention(dry_run=False, retention_days=30, batch_size=50, group_limit=10)

    assert report["groups_distilled"] == 1
    assert report["raw_records_purged"] == 2
    distilled = MemoryRecord.objects.get(source_kind=DISTILLED_SOURCE_KIND)
    assert distilled.memory_kind == MemoryRecord.MemoryKind.EPISODIC
    assert distilled.source_ref.endswith("scheduled_task_executed:key:scheduled-task-exec-bucket:task-1:daily_weather_report")
    assert distilled.content.startswith("Executed daily weather report recorded 2 times")
    assert MemoryRecord.objects.filter(source_kind="scheduled_task_executed").count() == 0


def test_run_memory_retention_is_idempotent_on_rerun():
    first = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 1 at 08:00 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    second = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Execution 2 at 08:02 with status ok",
        summary="Executed daily weather report",
        importance=0.20,
        source_kind="scheduled_task_executed",
        source_ref="task-1",
        dedupe_key="scheduled-task-exec-bucket:task-1:daily_weather_report",
        dedupe_mode="exact",
    )
    _age_records(first, second)

    first_report = run_memory_retention(dry_run=False, retention_days=30, batch_size=50, group_limit=10)
    second_report = run_memory_retention(dry_run=False, retention_days=30, batch_size=50, group_limit=10)

    assert first_report["groups_distilled"] == 1
    assert second_report["groups_distilled"] == 0
    assert second_report["directly_purged"] == 0
    assert MemoryRecord.objects.filter(source_kind=DISTILLED_SOURCE_KIND).count() == 1


def test_run_memory_retention_preserves_pinned_and_high_importance_and_purges_expired_noise():
    pinned = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Pinned episodic note.",
        importance=0.10,
        pinned=True,
        source_kind="manual_remember",
    )
    high_semantic = remember(
        "agent",
        "agent-retention",
        "semantic",
        "Important semantic fact.",
        importance=0.95,
        source_kind="manual_remember",
    )
    high_procedural = remember(
        "agent",
        "agent-retention",
        "procedural",
        "Important procedure.",
        importance=0.90,
        source_kind="manual_remember",
    )
    expired_noise = remember(
        "agent",
        "agent-retention",
        "episodic",
        "Low value expired noise.",
        importance=Decimal("0.10"),
        source_kind="manual_remember",
        expires_at=timezone.now() - timedelta(minutes=5),
    )
    _age_records(pinned, high_semantic, high_procedural)

    report = run_memory_retention(dry_run=False, retention_days=30, batch_size=50, group_limit=10)

    assert report["directly_purged"] == 1
    assert MemoryRecord.objects.filter(id=pinned.id).exists()
    assert MemoryRecord.objects.filter(id=high_semantic.id).exists()
    assert MemoryRecord.objects.filter(id=high_procedural.id).exists()
    assert not MemoryRecord.objects.filter(id=expired_noise.id).exists()
