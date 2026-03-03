import secrets
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class Transport(models.Model):
    MODE_CHOICES = [
        ("polling", "Polling"),
        ("webhook", "Webhook"),
        ("both", "Both"),
    ]

    key = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="polling")
    is_enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.display_name


class TransportEndpoint(models.Model):
    KIND_CHOICES = [
        ("bot", "Bot"),
        ("webhook", "Webhook"),
    ]

    transport = models.ForeignKey(Transport, on_delete=models.CASCADE, related_name="endpoints")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    config = models.JSONField(default=dict)

    def __str__(self) -> str:
        return f"{self.transport.key}:{self.kind}"


class ExternalIdentity(models.Model):
    transport = models.ForeignKey(Transport, on_delete=models.CASCADE, related_name="identities")
    external_user_id = models.CharField(max_length=128)
    username = models.CharField(max_length=128, blank=True)
    display_name = models.CharField(max_length=128, blank=True)
    is_allowed = models.BooleanField(default=True)
    role_hint = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = ("transport", "external_user_id")

    def __str__(self) -> str:
        return self.display_name or self.username or self.external_user_id


class CommsConversation(models.Model):
    transport = models.ForeignKey(Transport, on_delete=models.CASCADE, related_name="conversations")
    endpoint = models.ForeignKey(
        "TransportEndpoint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    external_conversation_id = models.CharField(max_length=255)
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    control_conversation = models.OneToOneField(
        "control.ControlConversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comms_conversation",
    )

    class Meta:
        unique_together = ("endpoint", "external_conversation_id")
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or self.external_conversation_id

    def __str__(self) -> str:
        return self.title or self.external_conversation_id


class CommsMessage(models.Model):
    DIRECTION_CHOICES = [("in", "Incoming"), ("out", "Outgoing")]

    conversation = models.ForeignKey(
        CommsConversation, on_delete=models.CASCADE, related_name="messages"
    )
    external_message_id = models.CharField(max_length=255, blank=True)
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES, default="in")
    sender = models.ForeignKey(
        ExternalIdentity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    text = models.TextField()
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.conversation}: {self.text[:40]}"


def generate_pair_code(length: int = 8) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PendingPairing(models.Model):
    STATUS_PENDING = "pending"
    STATUS_CLAIMED = "claimed"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CLAIMED, "Claimed"),
        (STATUS_EXPIRED, "Expired"),
    ]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        TransportEndpoint, on_delete=models.CASCADE, related_name="pairings"
    )
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_pairings",
    )
    pair_code = models.CharField(max_length=12, unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    claimed_chat_id = models.CharField(max_length=64, null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["endpoint", "pair_code"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.pair_code:
            self.pair_code = generate_pair_code()
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=30)
        super().save(*args, **kwargs)

    def is_active(self) -> bool:
        return self.status == self.STATUS_PENDING and self.expires_at > timezone.now()

    def mark_claimed(self, chat_id: str) -> None:
        self.claimed_chat_id = chat_id
        self.claimed_at = timezone.now()
        self.status = self.STATUS_CLAIMED
        self.save(update_fields=["claimed_chat_id", "claimed_at", "status"])
