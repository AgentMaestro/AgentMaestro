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
        PAUSED = "PAUSED", "Paused"
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
    locked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_summary = models.TextField(blank=True, default="")
    current_task_id = models.CharField(max_length=64, blank=True, default="")
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

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
        SUBRUN_SPAWN = "SUBRUN_SPAWN", "Subrun Spawn"
        ACTION = "ACTION", "Action"
        FINAL = "FINAL", "Final"
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
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "step_index"], name="uniq_step_index_per_run"),
        ]
        indexes = [
            models.Index(fields=["run", "step_index"]),
            models.Index(fields=["run", "kind", "created_at"]),
            models.Index(fields=["run", "correlation_id"]),
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
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "seq"], name="uniq_event_seq_per_run"),
        ]
        indexes = [
            models.Index(fields=["run", "seq"]),
            models.Index(fields=["run", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["run", "correlation_id"]),
        ]

    def __str__(self):
        return f"{self.run_id}#{self.seq} {self.event_type}"



class RunArchive(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="archives",
    )
    archive_path = models.TextField()
    summary = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["run", "created_at"]),
        ]

    def __str__(self):
        return f"Archive for {self.run_id} at {self.created_at.isoformat()}"


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


class SubrunLink(TimeStampedModel):
    class JoinPolicy(models.TextChoices):
        WAIT_ALL = "WAIT_ALL", "Wait for all subruns"
        WAIT_ANY = "WAIT_ANY", "Resume after any child completes"
        QUORUM = "QUORUM", "Resume after quorum"
        TIMEOUT = "TIMEOUT", "Resume on timeout"

    class FailurePolicy(models.TextChoices):
        FAIL_FAST = "FAIL_FAST", "Fail parent on child failure"
        IGNORE_FAILURE = "IGNORE_FAILURE", "Ignore child failures"
        CANCEL_SIBLINGS = "CANCEL_SIBLINGS", "Cancel siblings on failure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="subrun_links",
    )
    child_run = models.OneToOneField(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="subrun_link",
    )
    group_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    join_policy = models.CharField(
        max_length=16,
        choices=JoinPolicy.choices,
        default=JoinPolicy.WAIT_ALL,
    )
    quorum = models.PositiveIntegerField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(null=True, blank=True)
    failure_policy = models.CharField(
        max_length=32,
        choices=FailurePolicy.choices,
        default=FailurePolicy.FAIL_FAST,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["parent_run", "group_id"]),
            models.Index(fields=["parent_run", "child_run"]),
        ]

    def __str__(self):
        return f"SubrunLink {self.id} ({self.join_policy})"
