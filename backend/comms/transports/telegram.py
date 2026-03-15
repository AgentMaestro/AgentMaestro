"""Telegram transport adapter."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Mapping, Optional, TYPE_CHECKING

import httpx
from django.conf import settings

from .base import NormalizedEvent, NormalizedEventKind, TransportAdapter

if TYPE_CHECKING:
    from ..models import TransportEndpoint
else:
    TransportEndpoint = Any


class TelegramAdapter(TransportAdapter):
    BASE_URL = "https://api.telegram.org"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    def _bot_token(self, endpoint: TransportEndpoint) -> str:
        token = endpoint.config.get("bot_token")
        if token:
            return token

        token_env = endpoint.config.get("bot_token_env")
        if token_env:
            token = os.getenv(token_env)
        if not token:
            token = os.getenv("TELEGRAM_BOT_TOKEN") or getattr(settings, "TELEGRAM_BOT_TOKEN", None)

        if not token:
            raise RuntimeError("Telegram bot token not configured (set TELEGRAM_BOT_TOKEN)")
        return token

    def _build_api_url(self, token: str, method: str) -> str:
        return f"{self.BASE_URL}/bot{token}/{method}"

    async def poll_updates(
        self,
        endpoint: TransportEndpoint,
        offset: Optional[int] = None,
        timeout: int = 30,
        allowed_updates: Optional[list[str]] = None,
    ) -> list[Mapping[str, Any]]:
        token = self._bot_token(endpoint)
        params: Dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": allowed_updates or ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        request_timeout = httpx.Timeout(max(float(timeout) + 5.0, 10.0))
        resp = await self._client.get(
            self._build_api_url(token, "getUpdates"),
            params=params,
            timeout=request_timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("result", [])

    async def send_message(
        self,
        endpoint: TransportEndpoint,
        chat_id: str,
        text: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        token = self._bot_token(endpoint)
        body = {"chat_id": chat_id, "text": text}
        body.update(kwargs)
        resp = await self._client.post(self._build_api_url(token, "sendMessage"), json=body)
        resp.raise_for_status()
        return resp.json()

    async def edit_message(
        self,
        endpoint: TransportEndpoint,
        chat_id: str,
        message_id: str,
        text: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        token = self._bot_token(endpoint)
        body = {"chat_id": chat_id, "message_id": message_id, "text": text}
        body.update(kwargs)
        resp = await self._client.post(self._build_api_url(token, "editMessageText"), json=body)
        resp.raise_for_status()
        return resp.json()

    async def answer_callback_query(
        self,
        endpoint: TransportEndpoint,
        callback_query_id: str,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> Mapping[str, Any]:
        token = self._bot_token(endpoint)
        body: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            body["text"] = text
        if show_alert:
            body["show_alert"] = True
        resp = await self._client.post(self._build_api_url(token, "answerCallbackQuery"), json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_me(self, endpoint: TransportEndpoint) -> Mapping[str, Any]:
        token = self._bot_token(endpoint)
        resp = await self._client.get(self._build_api_url(token, "getMe"))
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("result", {})

    @staticmethod
    def normalize_update(update: Mapping[str, Any]) -> Iterable[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        update_id = int(update.get("update_id", 0))

        def build_event(
            kind: NormalizedEventKind,
            payload: Mapping[str, Any],
            text: Optional[str],
            message_id: Optional[int],
            callback_query_id: Optional[str],
            callback_data: Optional[str],
            ts: Optional[int],
            from_payload: Optional[Mapping[str, Any]] = None,
        ) -> None:
            chat = payload.get("chat") or {}
            from_user = from_payload or payload.get("from") or {}
            events.append(
                NormalizedEvent(
                    kind=kind,
                    update_id=update_id,
                    chat_id=str(chat.get("id", "")),
                    from_user_id=str(from_user.get("id", "")),
                    from_username=from_user.get("username"),
                    text=text,
                    message_id=str(message_id) if message_id else None,
                    callback_data=callback_data,
                    callback_query_id=callback_query_id,
                    ts=ts,
                )
            )

        if "message" in update:
            message = update["message"]
            build_event(
                "message",
                message,
                message.get("text"),
                message.get("message_id"),
                None,
                None,
                message.get("date"),
            )
        if "callback_query" in update:
            callback = update["callback_query"]
            message = callback.get("message") or {}
            build_event(
                "callback",
                message,
                callback.get("data"),
                message.get("message_id"),
                callback.get("id"),
                callback.get("data"),
                callback.get("date") or message.get("date"),
                callback.get("from") or {},
            )
        return events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._client.aclose()
