import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import TimeStampedModel, Workspace
from runs.models import AgentRun, AgentStep


class ToolRisk(models.TextChoices):
    SAFE = "SAFE", "Safe"
    ELEVATED = "ELEVATED", "Elevated"
    DANGEROUS = "DANGEROUS", "Dangerous"


class ToolGroup(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tool(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True, default="")
    required_parameters = models.JSONField(default=list, blank=True)
    tool_group = models.ForeignKey(
        ToolGroup, on_delete=models.PROTECT, related_name="tools"
    )
    risk = models.CharField(max_length=12, choices=ToolRisk.choices, default=ToolRisk.SAFE)
    args_schema = models.JSONField(default=dict, blank=True)
    requires_approval = models.BooleanField(default=False)
    released = models.BooleanField(default=True)

    class Meta:
        ordering = ["tool_group__name", "-risk", "name"]

    def save(self, *args, **kwargs):
        base = slugify(self.name) or "tool"
        slug_candidate = base
        suffix = 1
        while Tool.objects.filter(slug=slug_candidate).exclude(pk=self.pk).exists():
            suffix_value = f"-{suffix}"
            slug_candidate = f"{base[:120 - len(suffix_value)]}{suffix_value}"
            suffix += 1
        self.slug = slug_candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ToolDefinition(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tool_definitions",
    )
    tool = models.ForeignKey(
        Tool,
        on_delete=models.CASCADE,
        related_name="workspace_definitions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True, default="")
    args_schema = models.JSONField(default=dict, blank=True)
    default_risk_level = models.CharField(
        max_length=12,
        choices=ToolRisk.choices,
        default=ToolRisk.SAFE,
    )
    default_requires_approval = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "tool"],
                name="tools_tooldefinition_workspace_tool_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "tool"]),
            models.Index(fields=["workspace", "enabled"]),
        ]

    def __str__(self):
        tool_label = self.tool.name if self.tool else self.name
        return f"{self.workspace}:{tool_label}"


class AgentToolGrant(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.CASCADE,
        related_name="tool_grants",
    )
    tool = models.ForeignKey(
        Tool,
        on_delete=models.CASCADE,
        related_name="agent_grants",
    )
    enabled = models.BooleanField(default=False)

    class Meta:
        unique_together = [("agent", "tool")]
        indexes = [
            models.Index(fields=["agent", "tool"]),
        ]

    def __str__(self):
        status = "enabled" if self.enabled else "disabled"
        return f"{self.agent}:{self.tool} ({status})"


class ToolApprovalGrant(TimeStampedModel):
    class ScopeType(models.TextChoices):
        EXACT_PATH = "EXACT_PATH", "Exact Path"
        PATH_PREFIX = "PATH_PREFIX", "Path Prefix"
        REPO_EXACT = "REPO_EXACT", "Repository"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tool_approval_grants",
    )
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="tool_approval_grants",
    )
    tool_name = models.CharField(max_length=80)
    scope_type = models.CharField(
        max_length=16,
        choices=ScopeType.choices,
    )
    scope_path = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tool_approval_grants",
    )
    source_tool_call = models.ForeignKey(
        "tools.ToolCall",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spawned_approval_grants",
    )
    metadata = models.JSONField(default=dict, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_tool_approval_grants",
    )

    class Meta:
        indexes = [
            models.Index(fields=["run", "tool_name", "revoked_at"]),
            models.Index(fields=["workspace", "run", "revoked_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, user) -> None:
        self.revoked_at = timezone.now()
        self.revoked_by = user

    def __str__(self):
        return f"{self.run_id}:{self.tool_name}:{self.scope_type}"


class ToolCall(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        DENIED = "DENIED", "Denied"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

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
        max_length=16,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_tool_calls",
    )
    approval_grant = models.ForeignKey(
        "tools.ToolApprovalGrant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_tool_calls",
    )
    approval_metadata = models.JSONField(default=dict, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    exit_code = models.BigIntegerField(null=True, blank=True)
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    provider_call_id = models.CharField(max_length=128, blank=True, default="")
    provider_response_id = models.CharField(max_length=128, blank=True, default="")
    celery_task_id = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["run", "status", "created_at"]),
            models.Index(fields=["tool_name", "created_at"]),
            models.Index(fields=["run", "observed_at"]),
        ]

    def mark_approved(self, user, *, approval_grant=None, approval_metadata=None):
        self.status = self.Status.QUEUED
        self.approved_by = user
        self.approval_grant = approval_grant
        self.approval_metadata = approval_metadata or {}
        self.approved_at = timezone.now()

    def __str__(self):
        return f"{self.run_id} tool call {self.tool_name} ({self.status})"
