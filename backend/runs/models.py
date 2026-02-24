import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from agents.models import Agent
from core.models import TimeStampedModel, Workspace


class AgentRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL", "Waiting for Approval"
        WAITING_FOR_TOOL = "WAITING_FOR_TOOL", "Waiting for Tool"
        WAITING_FOR_SUBRUN = "WAITING_FOR_SUBRUN", "Waiting for Subrun"
        WAITING_FOR_USER = "WAITING_FOR_USER", "Waiting for User"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        CANCELED = "CANCELED", "Canceled"

    class Channel(models.TextChoices):
        DASHBOARD = "DASHBOARD", "Dashboard"
        TELEGRAM = "TELEGRAM", "Telegram"
        API = "API", "API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.PROTECT,
        related_name="runs",
    )
    parent_run = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_runs",
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_runs_started",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    channel = models.CharField(
        max_length=12,
        choices=Channel.choices,
        default=Channel.DASHBOARD,
        db_index=True,
    )
    input_text = models.TextField(blank=True, default="")
    final_text = models.TextField(blank=True, default="")
    current_step_index = models.IntegerField(default=0)
    cancel_requested = models.BooleanField(default=False)
    max_steps = models.IntegerField(default=80)
    max_tool_calls = models.IntegerField(default=40)
    locked_by = models.CharField(max_length=120, blank=True, default="")
    lock_expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_summary = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["workspace", "status", "started_at"]),
            models.Index(fields=["agent", "started_at"]),
            models.Index(fields=["parent_run", "created_at"]),
        ]

    def __str__(self):
        return f"Run {self.id} ({self.status})"


class AgentStep(TimeStampedModel):
    class Kind(models.TextChoices):
        PLAN = "PLAN", "Plan"
        MODEL_CALL = "MODEL_CALL", "Model Call"
        TOOL_CALL = "TOOL_CALL", "Tool Call"
        OBSERVATION = "OBSERVATION", "Observation"
        MESSAGE = "MESSAGE", "Message"
        SPAWN_SUBRUN = "SPAWN_SUBRUN", "Spawn Subrun"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    step_index = models.IntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "step_index"], name="uniq_step_index_per_run"),
        ]
        indexes = [
            models.Index(fields=["run", "step_index"]),
            models.Index(fields=["run", "kind", "created_at"]),
        ]

    def __str__(self):
        return f"{self.run_id} step {self.step_index} {self.kind}"


class RunEvent(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="events",
    )
    seq = models.BigIntegerField()
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "seq"], name="uniq_event_seq_per_run"),
        ]
        indexes = [
            models.Index(fields=["run", "seq"]),
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.run_id}#{self.seq} {self.event_type}"


class Artifact(TimeStampedModel):
    class Type(models.TextChoices):
        FILE = "FILE", "File"
        TEXT = "TEXT", "Text"
        IMAGE = "IMAGE", "Image"
        JSON = "JSON", "JSON"
        REPORT = "REPORT", "Report"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    type = models.CharField(max_length=12, choices=Type.choices, default=Type.FILE)
    name = models.CharField(max_length=180, blank=True, default="")
    mime_type = models.CharField(max_length=120, blank=True, default="")
    storage_path = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "type", "created_at"]),
        ]
