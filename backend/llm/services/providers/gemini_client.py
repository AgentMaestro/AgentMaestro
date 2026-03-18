import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from django.conf import settings

from .base import BaseLLMClient
from .gemini_http import GeminiHTTPService

logger = logging.getLogger(__name__)
DEFAULT_TRANSPORT = (getattr(settings, "GEMINI_TRANSPORT", None) or os.getenv("GEMINI_TRANSPORT") or "http").lower()


class GeminiClient(BaseLLMClient):
    provider_name = "gemini"
    transport = "http"

    def __init__(self):
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        base_url = (
                settings.GEMINI_BASE_URL
                or os.getenv("GEMINI_BASE_URL")
                or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        self.api_key = api_key
        self.base_url = base_url
        self.http_service = GeminiHTTPService(
            base_url=self.base_url,
            api_key=self.api_key,
            client_name="agentmaestro/gemini",
        )
        self.transport = DEFAULT_TRANSPORT

    def preferred_transport(self) -> str:
        return (
                settings.GEMINI_TRANSPORT
                or os.getenv("GEMINI_TRANSPORT", self.transport)
                or self.transport
        ).lower()

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
        return await self.http_service.complete(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            extra=extra,
        )
