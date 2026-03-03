from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class WebhookHandler(ABC):
    """Base contract for future webhook-based transports."""

    @abstractmethod
    def handle(self, transport_key: str, payload: Mapping[str, Any]) -> Any:
        raise NotImplementedError()
