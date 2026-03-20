from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from core.models import TimeStampedModel, Workspace


def _fernet() -> Fernet:
    secret = str(getattr(settings, "SECRET_KEY", "dev-secret-key")).encode("utf-8")
    key = urlsafe_b64encode(sha256(secret).digest())
    return Fernet(key)


def _encrypt(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    return _fernet().encrypt(text.encode("utf-8"))


def _decrypt(value: bytes | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(bytes(value)).decode("utf-8")
    except InvalidToken:
        return ""


class GoogleAccount(TimeStampedModel):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="google_accounts")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_accounts")
    google_subject = models.CharField(max_length=255, blank=True, default="", db_index=True)
    email = models.CharField(max_length=255, blank=True, default="", db_index=True)
    scopes = models.JSONField(default=list, blank=True)
    access_token_ciphertext = models.BinaryField(blank=True, default=b"")
    refresh_token_ciphertext = models.BinaryField(blank=True, default=b"")
    token_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["workspace", "owner", "is_active"]),
            models.Index(fields=["workspace", "google_subject"]),
            models.Index(fields=["email", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.email or self.google_subject or f"GoogleAccount:{self.pk}"

    @property
    def access_token(self) -> str:
        return _decrypt(self.access_token_ciphertext)

    @property
    def refresh_token(self) -> str:
        return _decrypt(self.refresh_token_ciphertext)

    def set_tokens(self, *, access_token: str = "", refresh_token: str = "") -> None:
        if access_token:
            self.access_token_ciphertext = _encrypt(access_token)
        if refresh_token:
            self.refresh_token_ciphertext = _encrypt(refresh_token)

    def clear_tokens(self) -> None:
        self.access_token_ciphertext = b""
        self.refresh_token_ciphertext = b""


User = get_user_model()
