from __future__ import annotations

from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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


class RecurrenceRule(TimeStampedModel):
    class Frequency(models.TextChoices):
        HOURLY = "hourly", "Hourly"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        SEMIANNUAL = "semiannual", "Semiannual"
        ANNUAL = "annual", "Annual"

    class WeekdayCode(models.TextChoices):
        MONDAY = "mon", "Monday"
        TUESDAY = "tue", "Tuesday"
        WEDNESDAY = "wed", "Wednesday"
        THURSDAY = "thu", "Thursday"
        FRIDAY = "fri", "Friday"
        SATURDAY = "sat", "Saturday"
        SUNDAY = "sun", "Sunday"

    name = models.CharField(max_length=160, blank=True, default="")
    timezone = models.CharField(max_length=64)
    frequency = models.CharField(max_length=24, choices=Frequency.choices)
    interval = models.PositiveIntegerField(default=1)
    by_weekday = models.JSONField(default=list, blank=True)
    by_month_day = models.JSONField(default=list, blank=True)
    week_of_month = models.SmallIntegerField(null=True, blank=True)
    weekday_of_month = models.CharField(max_length=3, choices=WeekdayCode.choices, blank=True, default="")
    by_month = models.JSONField(default=list, blank=True)
    local_time = models.TimeField(null=True, blank=True)
    run_minute = models.PositiveSmallIntegerField(null=True, blank=True)
    window_start_time = models.TimeField(null=True, blank=True)
    window_end_time = models.TimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "frequency", "timezone"]),
            models.Index(fields=["timezone", "frequency"]),
        ]

    def clean(self):
        from memory.recurrence import normalize_recurrence_rule_data

        normalized = normalize_recurrence_rule_data(
            {
                "name": self.name,
                "timezone": self.timezone,
                "frequency": self.frequency,
                "interval": self.interval,
                "by_weekday": self.by_weekday,
                "by_month_day": self.by_month_day,
                "week_of_month": self.week_of_month,
                "weekday_of_month": self.weekday_of_month,
                "by_month": self.by_month,
                "local_time": self.local_time,
                "run_minute": self.run_minute,
                "window_start_time": self.window_start_time,
                "window_end_time": self.window_end_time,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "is_active": self.is_active,
            }
        )
        self.name = str(normalized["name"])
        self.timezone = str(normalized["timezone"])
        self.frequency = str(normalized["frequency"])
        self.interval = int(normalized["interval"])
        self.by_weekday = list(normalized["by_weekday"])
        self.by_month_day = list(normalized["by_month_day"])
        self.week_of_month = normalized["week_of_month"]
        self.weekday_of_month = str(normalized["weekday_of_month"])
        self.by_month = list(normalized["by_month"])
        self.local_time = normalized["local_time"]
        self.run_minute = normalized["run_minute"]
        self.window_start_time = normalized["window_start_time"]
        self.window_end_time = normalized["window_end_time"]
        self.start_date = normalized["start_date"]
        self.end_date = normalized["end_date"]
        self.is_active = bool(normalized["is_active"])

    @property
    def summary(self) -> str:
        from memory.recurrence import describe_recurrence_rule

        return describe_recurrence_rule(self)

    def __str__(self) -> str:
        return self.name or self.summary


class ScheduledTask(TimeStampedModel):
    class TaskType(models.TextChoices):
        DAILY_WEATHER_REPORT = "daily_weather_report", "Daily Weather Report"
        DAILY_EMAIL_CHECK = "daily_email_check", "Daily Email Check"
        DAILY_CALENDAR_CHECK = "daily_calendar_check", "Daily Calendar Check"
        OTHER_DAILY_TASK = "other_daily_task", "Other Daily Task"
        OTHER_TASK = "other_task", "Other Task"

    class ScheduleKind(models.TextChoices):
        DAILY_TIME = "daily_time", "Daily Time"
        RECURRENCE_RULE = "recurrence_rule", "Recurrence Rule"

    class DeliveryTarget(models.TextChoices):
        PAIRED_TRANSPORT = "paired_transport", "Paired Transport"

    class ExecutionMode(models.TextChoices):
        DETERMINISTIC = "deterministic", "Deterministic"
        HEADLESS_RUN = "headless_run", "Headless Run"

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
    recurrence_rule = models.ForeignKey(
        RecurrenceRule,
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
    execution_mode = models.CharField(
        max_length=24,
        choices=ExecutionMode.choices,
        default=ExecutionMode.DETERMINISTIC,
        db_index=True,
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
    last_run = models.ForeignKey(
        "runs.AgentRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_tasks_as_last_run",
    )
    active_run = models.ForeignKey(
        "runs.AgentRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_tasks_as_active_run",
    )

    class Meta:
        indexes = [
            models.Index(fields=["enabled", "next_run_at"]),
            models.Index(fields=["agent", "enabled", "next_run_at"]),
            models.Index(fields=["workspace", "enabled", "next_run_at"]),
            models.Index(fields=["task_type", "enabled", "next_run_at"]),
            models.Index(fields=["enabled", "execution_mode", "next_run_at"]),
        ]

    @property
    def recurrence_summary(self) -> str:
        return self.recurrence_rule.summary

    def clean(self):
        if self.recurrence_rule_id is None:
            raise ValidationError({"recurrence_rule": ["Scheduled tasks require a recurrence rule."]})
        self.timezone = self.recurrence_rule.timezone
        if self.recurrence_rule.local_time is not None:
            self.local_time = self.recurrence_rule.local_time
        self.schedule_kind = (
            self.ScheduleKind.DAILY_TIME
            if self.recurrence_rule.frequency == RecurrenceRule.Frequency.DAILY and not self.recurrence_rule.by_weekday
            else self.ScheduleKind.RECURRENCE_RULE
        )

    def __str__(self) -> str:
        label = self.title or self.get_task_type_display()
        return f"{label} @ {self.recurrence_summary}"


class ScheduledTaskApproval(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheduled_task = models.ForeignKey(
        ScheduledTask,
        on_delete=models.CASCADE,
        related_name="approvals",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="scheduled_task_approvals",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.PROTECT,
        related_name="scheduled_task_approvals",
    )
    source_run = models.ForeignKey(
        "runs.AgentRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_task_approvals",
    )
    source_tool_call = models.ForeignKey(
        "tools.ToolCall",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_task_approvals",
    )
    fingerprint = models.CharField(max_length=64, db_index=True)
    fingerprint_version = models.PositiveSmallIntegerField(default=1)
    execution_mode = models.CharField(max_length=24, default=ScheduledTask.ExecutionMode.HEADLESS_RUN)
    task_type = models.CharField(max_length=48)
    delivery_target = models.CharField(max_length=32, blank=True, default="")
    normalized_execution_payload = models.JSONField(default=dict, blank=True)
    tool_signature = models.JSONField(default=list, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_task_approvals",
    )
    approved_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["scheduled_task", "fingerprint", "fingerprint_version"]),
            models.Index(fields=["scheduled_task", "revoked_at", "expires_at"]),
            models.Index(fields=["agent", "approved_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())

    def __str__(self) -> str:
        return f"{self.scheduled_task_id}:{self.fingerprint[:12]}"


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
