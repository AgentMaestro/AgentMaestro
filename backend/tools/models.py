import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel, Workspace
from runs.models import AgentRun, AgentStep


class ToolDefinition(TimeStampedModel):
    class Risk(models.TextChoices):
        SAFE = "SAFE", "Safe"
        ELEVATED = "ELEVATED", "Elevated"
        DANGEROUS = "DANGEROUS", "Dangerous"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tool_definitions",
    )
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True, default="")
    args_schema = models.JSONField(default=dict, blank=True)
    default_risk_level = models.CharField(
        max_length=12,
        choices=Risk.choices,
        default=Risk.SAFE,
    )
    default_requires_approval = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("workspace", "name")]
        indexes = [
            models.Index(fields=["workspace", "name"]),
            models.Index(fields=["workspace", "enabled"]),
        ]

    def __str__(self):
        return f"{self.workspace}:{self.name}"


class ToolCall(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        CANCELED = "CANCELED", "Canceled"

    class Risk(models.TextChoices):
        SAFE = "SAFE", "Safe"
        ELEVATED = "ELEVATED", "Elevated"
        DANGEROUS = "DANGEROUS", "Dangerous"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    step = models.ForeignKey(
        AgentStep,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    tool_name = models.CharField(max_length=80)
    args = models.JSONField(default=dict, blank=True)
    risk_level = models.CharField(
        max_length=12,
        choices=Risk.choices,
        default=Risk.SAFE,
    )
    requires_approval = models.BooleanField(default=False)
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_tool_calls",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.IntegerField(null=True, blank=True)
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "status", "created_at"]),
            models.Index(fields=["tool_name", "created_at"]),
            models.Index(fields=["run", "observed_at"]),
        ]

    def mark_approved(self, user):
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()

    def __str__(self):
        return f"{self.run_id} tool call {self.tool_name} ({self.status})"
