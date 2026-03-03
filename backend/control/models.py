import uuid

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone


User = get_user_model()


class Role(models.Model):
    key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return self.name


class Operator(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    roles = models.ManyToManyField(Role, blank=True, related_name="operators")
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.user.get_username()


class ControlConversation(models.Model):
    KIND_CHOICES = [
        ("comms_mirror", "Comms Mirror"),
        ("internal_only", "Internal Only"),
        ("run_thread", "Run Thread"),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    title = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or str(self.uuid)


class ControlMessage(models.Model):
    DIRECTION_CHOICES = [
        ("in", "Incoming"),
        ("out", "Outgoing"),
        ("system", "System"),
    ]
    AUTHOR_CHOICES = [
        ("operator", "Operator"),
        ("agent", "Agent"),
        ("transport_user", "Transport User"),
        ("system", "System"),
    ]

    conversation = models.ForeignKey(
        ControlConversation, on_delete=models.CASCADE, related_name="messages"
    )
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    author_type = models.CharField(max_length=16, choices=AUTHOR_CHOICES)
    author_label = models.CharField(max_length=128)
    text = models.TextField()
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    source_transport = models.CharField(max_length=64, null=True, blank=True)
    source_conversation_id = models.CharField(max_length=128, null=True, blank=True)
    source_message_id = models.CharField(max_length=128, null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.author_label}: {self.text[:40]}"


class IngestEvent(models.Model):
    transport = models.CharField(max_length=64)
    external_event_id = models.CharField(max_length=255)
    received_at = models.DateTimeField(default=timezone.now)
    result_meta = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ("transport", "external_event_id")
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.transport}:{self.external_event_id}"


class ApprovalRequest(models.Model):
    RISK_LEVEL_EXTERNAL_WRITE = "external_write"
    RISK_LEVEL_DESTRUCTIVE = "destructive"
    RISK_LEVEL_ELEVATED = "elevated"
    RISK_LEVEL_OTHER = "other"

    RISK_LEVEL_CHOICES = [
        (RISK_LEVEL_EXTERNAL_WRITE, "External Write"),
        (RISK_LEVEL_DESTRUCTIVE, "Destructive"),
        (RISK_LEVEL_ELEVATED, "Elevated"),
        (RISK_LEVEL_OTHER, "Other"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DENIED, "Denied"),
        (STATUS_EXPIRED, "Expired"),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    run_id = models.CharField(max_length=64, blank=True, null=True)
    risk_level = models.CharField(
        max_length=32, choices=RISK_LEVEL_CHOICES, default=RISK_LEVEL_EXTERNAL_WRITE
    )
    tool_name = models.CharField(max_length=128)
    summary = models.TextField(blank=True)
    payload_preview = models.JSONField(default=dict, blank=True, null=True)
    constraints = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        Operator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.tool_name} ({self.status})"


class ApprovalGrant(models.Model):
    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grants",
    )
    scope = models.CharField(max_length=128)
    constraints = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_persistent = models.BooleanField(default=False)
    granted_by = models.ForeignKey(
        Operator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grants",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.scope} grant"
