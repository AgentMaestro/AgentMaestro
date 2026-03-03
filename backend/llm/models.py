import uuid
from django.db import models
from django.utils import timezone


class AgentRole(models.TextChoices):
    PLANNER = "planner", "Planner"
    CODER = "coder", "Coder"
    GENERIC = "generic", "Generic"


class MessageRole(models.TextChoices):
    SYSTEM = "system", "System"
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    TOOL = "tool", "Tool"


class RunStatus(models.TextChoices):
    STARTED = "started", "Started"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class LLMModelProfile(models.Model):
    name = models.CharField(max_length=100, unique=True)
    agent_role = models.CharField(
        max_length=20, choices=AgentRole.choices, default=AgentRole.GENERIC
    )
    provider = models.CharField(max_length=50, default="openai")
    model = models.CharField(max_length=200)
    reasoning_model = models.CharField(max_length=200, blank=True, null=True)
    temperature = models.FloatField(blank=True, null=True)
    max_output_tokens = models.IntegerField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    extra = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.provider}:{self.model})"


class LLMRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=200)
    profile = models.ForeignKey(
        LLMModelProfile, related_name="runs", on_delete=models.SET_NULL, blank=True, null=True
    )
    orchestration_run_id = models.UUIDField(blank=True, null=True)
    agent_name = models.CharField(max_length=100, blank=True)
    purpose = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.STARTED)
    token_prompt = models.IntegerField(blank=True, null=True)
    token_completion = models.IntegerField(blank=True, null=True)
    token_total = models.IntegerField(blank=True, null=True)
    provider_meta = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"LLMRun {self.id} ({self.provider}:{self.model})"


class LLMMessage(models.Model):
    run = models.ForeignKey(LLMRun, related_name="messages", on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)
    role = models.CharField(max_length=20, choices=MessageRole.choices)
    content = models.TextField(blank=True)
    name = models.CharField(max_length=100, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role} message in {self.run_id}"


class LLMToolCall(models.Model):
    run = models.ForeignKey(LLMRun, related_name="tool_calls", on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)
    tool_name = models.CharField(max_length=200)
    arguments = models.JSONField(default=dict, blank=True)
    result = models.JSONField(blank=True, null=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.tool_name} call for {self.run_id}"
