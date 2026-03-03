from __future__ import annotations

from typing import Any, Mapping

from .base import WebhookHandler


def dispatch_webhook(transport_key: str, payload: Mapping[str, Any], handler: WebhookHandler) -> Any:
    """Execute a webhook handler (future extension point)."""
    return handler.handle(transport_key, payload)

