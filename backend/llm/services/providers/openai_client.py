import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from openai import AsyncOpenAI
from django.conf import settings
from .base import BaseLLMClient
from .common import messages_to_system_and_user_input
from .openai_http import OpenAIHTTPService
from .openai_ws import (
    OpenAIResponsesWSClient,
    OpenAIResponsesWSException,
    OpenAIResponsesWSSessionPool,
    OpenAIResponsesWSPreviousResponseNotFound,
)
from .retry import is_openai_compatible_transient_error

logger = logging.getLogger(__name__)
DEFAULT_TRANSPORT = settings.OPENAI_TRANSPORT or os.getenv("OPENAI_TRANSPORT", "ws").lower()


class OpenAIClient(BaseLLMClient):
    provider_name = "openai"

    def __init__(self):
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        base_url = settings.OPENAI_BASE_URL or os.getenv("OPENAI_BASE_URL")
        self.api_key = api_key
        self.base_url = base_url
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.http_service = OpenAIHTTPService(self.client)
        self.transport = DEFAULT_TRANSPORT
        self.ws_timeout = float(settings.OPENAI_WS_TIMEOUT_SECONDS or  os.getenv("OPENAI_WS_TIMEOUT_SECONDS", "120"))
        self.ws_idle_timeout = float(settings.OPENAI_WS_IDLE_TIMEOUT_SECONDS or os.getenv("OPENAI_WS_IDLE_TIMEOUT_SECONDS", "60"))
        self.ws_session_pool = OpenAIResponsesWSSessionPool(
            api_key=api_key,
            base_url=base_url,
            idle_timeout_seconds=self.ws_idle_timeout,
            timeout_seconds=self.ws_timeout,
        )

    def preferred_transport(self) -> str:
        return (settings.OPENAI_TRANSPORT or os.getenv("OPENAI_TRANSPORT", self.transport)).lower()

    def supports_ws_transport(self) -> bool:
        return True

    def is_transient_error(self, exc: Exception) -> bool:
        return is_openai_compatible_transient_error(exc)

    def is_ws_exception(self, exc: Exception) -> bool:
        return isinstance(exc, OpenAIResponsesWSException)

    def is_previous_response_not_found(self, exc: Exception) -> bool:
        return isinstance(exc, OpenAIResponsesWSPreviousResponseNotFound)

    def build_error_meta(self, exc: Exception) -> Dict[str, Any]:
        meta = super().build_error_meta(exc)
        meta.update(
            {
                "classification": str(getattr(exc, "classification", "") or "").strip(),
                "code": str(getattr(exc, "code", "") or "").strip(),
                "param": str(getattr(exc, "param", "") or "").strip(),
                "status": getattr(exc, "status", None),
                "request_id": str(getattr(exc, "request_id", "") or "").strip(),
            }
        )
        return meta

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        transport = self.resolve_transport()
        if transport == "ws":
            return await self._complete_ws(messages, model, tools=tools)
        return await self.http_service.complete(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            extra=extra,
        )

    async def _complete_ws(
        self,
        messages: Sequence[Dict[str, Any]],
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        system_text, user_text = self._messages_to_input(messages)
        ws_client = OpenAIResponsesWSClient(
            self.api_key, base_url=self.base_url, timeout=self.ws_timeout
        )
        response = await ws_client.create_response(
            model=model, input_text=user_text, system_text=system_text
        )
        return {
            "text": response["text"],
            "tool_calls": response.get("tool_calls", []),
            "usage": {},
            "raw": response["raw"],
            "response_id": response.get("response_id"),
        }

    def _messages_to_input(
        self, messages: Sequence[Dict[str, Any]]
    ) -> tuple[Optional[str], str]:
        return messages_to_system_and_user_input(messages)

    async def stream_text(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("OpenAI streaming not implemented yet")

    async def get_ws_session(self, run_id: str, model: str, *, agent_id: str | None = None):
        return await self.ws_session_pool.get(run_id, model, agent_id=agent_id)

    async def close_ws_session(self, run_id: str, *, model: str | None = None) -> None:
        await self.ws_session_pool.close(run_id, model=model)

    async def cleanup_ws_sessions(self) -> None:
        await self.ws_session_pool.cleanup()
