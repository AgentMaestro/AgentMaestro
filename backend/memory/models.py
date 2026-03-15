from __future__ import annotations

from decimal import Decimal
import uuid

from django.conf import settings
from django.db import models

from agents.models import Agent
from core.models import TimeStampedModel, Workspace


class MemoryRecord(TimeStampedModel):
    class ScopeType(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        AGENT = "agent", "Agent"
        USER = "user", "User"

    class MemoryKind(models.TextChoices):
        EPISODIC = "episodic", "Episodic"
        SEMANTIC = "semantic", "Semantic"
        PROCEDURAL = "procedural", "Procedural"

    scope_type = models.CharField(max_length=24, choices=ScopeType.choices)
    scope_id = models.CharField(max_length=128, db_index=True)
    memory_kind = models.CharField(max_length=24, choices=MemoryKind.choices)
    dedupe_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    source_kind = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source_ref = models.CharField(max_length=255, blank=True, default="", db_index=True)
    content = models.TextField()
    summary = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    importance = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.50"))
    access_count = models.PositiveIntegerField(default=0)
    pinned = models.BooleanField(default=False, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope_type", "scope_id", "memory_kind"]),
            models.Index(fields=["scope_type", "scope_id", "updated_at"]),
            models.Index(fields=["memory_kind", "updated_at"]),
            models.Index(fields=["scope_type", "scope_id", "memory_kind", "dedupe_key"]),
            models.Index(fields=["source_kind", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.scope_type}:{self.scope_id}:{self.memory_kind}"


class ScheduledTask(TimeStampedModel):
    class TaskType(models.TextChoices):
        DAILY_WEATHER_REPORT = "daily_weather_report", "Daily Weather Report"
        DAILY_EMAIL_CHECK = "daily_email_check", "Daily Email Check"
        OTHER_DAILY_TASK = "other_daily_task", "Other Daily Task"

    class ScheduleKind(models.TextChoices):
        DAILY_TIME = "daily_time", "Daily Time"

    class DeliveryTarget(models.TextChoices):
        PAIRED_TRANSPORT = "paired_transport", "Paired Transport"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="scheduled_tasks",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.PROTECT,
        related_name="scheduled_tasks",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="scheduled_tasks",
    )
    source_memory = models.ForeignKey(
        MemoryRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_tasks",
    )
    title = models.CharField(max_length=160, blank=True, default="")
    task_type = models.CharField(max_length=48, choices=TaskType.choices)
    schedule_kind = models.CharField(
        max_length=24,
        choices=ScheduleKind.choices,
        default=ScheduleKind.DAILY_TIME,
    )
    delivery_target = models.CharField(
        max_length=32,
        choices=DeliveryTarget.choices,
        default=DeliveryTarget.PAIRED_TRANSPORT,
    )
    timezone = models.CharField(max_length=64, default="UTC")
    local_time = models.TimeField()
    next_run_at = models.DateTimeField(db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_success_at = models.DateTimeField(null=True, blank=True, db_index=True)
    enabled = models.BooleanField(default=True, db_index=True)
    execution_payload = models.JSONField(default=dict, blank=True)
    last_result_summary = models.TextField(blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["enabled", "next_run_at"]),
            models.Index(fields=["agent", "enabled", "next_run_at"]),
            models.Index(fields=["workspace", "enabled", "next_run_at"]),
            models.Index(fields=["task_type", "enabled", "next_run_at"]),
        ]

    def __str__(self) -> str:
        label = self.title or self.get_task_type_display()
        return f"{label} @ {self.local_time.isoformat(timespec='minutes')}"


class MemoryHealthSnapshot(TimeStampedModel):
    retention_days = models.PositiveIntegerField(default=30)
    compare_days = models.PositiveIntegerField(default=30)
    total_records = models.PositiveIntegerField(default=0, db_index=True)
    pinned_records = models.PositiveIntegerField(default=0)
    high_importance_records = models.PositiveIntegerField(default=0)
    retention_candidate_records = models.PositiveIntegerField(default=0)
    scheduled_tasks_total = models.PositiveIntegerField(default=0)
    report_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at", "total_records"]),
        ]

    def __str__(self) -> str:
        return f"Memory health @ {self.created_at.isoformat()}"
