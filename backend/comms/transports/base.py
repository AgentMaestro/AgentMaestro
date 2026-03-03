from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import TransportEndpoint
else:
    TransportEndpoint = Any

NormalizedEventKind = Literal['message', 'callback']


@dataclass
class NormalizedEvent:
    kind: NormalizedEventKind
    update_id: int
    chat_id: str
    from_user_id: str
    from_username: Optional[str]
    text: Optional[str]
    message_id: Optional[str]
    callback_data: Optional[str]
    callback_query_id: Optional[str]
    ts: Optional[int]


class TransportAdapter(ABC):
    @abstractmethod
    async def poll_updates(
        self,
        endpoint: TransportEndpoint,
        offset: Optional[int] = None,
        timeout: int = 30,
    ) -> list[Mapping[str, Any]]:
        """Return raw updates pulled from transport."""

    @abstractmethod
    async def send_message(
        self,
        endpoint: TransportEndpoint,
        chat_id: str,
        text: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """Send outbound message through transport."""

    @abstractmethod
    async def edit_message(
        self,
        endpoint: TransportEndpoint,
        chat_id: str,
        message_id: str,
        text: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """Edit an existing message."""

    @abstractmethod
    def normalize_update(self, update: Mapping[str, Any]) -> Iterable[NormalizedEvent]:
        """Normalize raw update to internal schema."""
