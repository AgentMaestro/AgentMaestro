import os
from typing import Any, Dict, List, Optional, Sequence

from django.conf import settings

from .base import BaseLLMClient
from .gemini_http import GeminiHTTPService
from ..model_failover import is_retryable_model_failure



def _resolve_gemini_transport(default: str = "http") -> str:
    configured = getattr(settings, "GEMINI_TRANSPORT", None)
    if configured:
        return str(configured).lower()
    env_value = os.getenv("GEMINI_TRANSPORT")
    if env_value:
        return env_value.lower()
    return default.lower()


DEFAULT_TRANSPORT = _resolve_gemini_transport()


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
        return _resolve_gemini_transport(self.transport)

    async def complete(
            self,
            messages: Sequence[Dict[str, Any]],
            *,
            model: str,
            tools: Optional[List[Dict[str, Any]]] = None,
            temperature: Optional[float] = None,
            max_output_tokens: Optional[int] = None,
            previous_response_id: Optional[str] = None,
            outstanding_provider_call_id: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await self.http_service.complete(
            messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            previous_response_id=previous_response_id,
            outstanding_provider_call_id=outstanding_provider_call_id,
            extra=extra,
        )

    def is_transient_error(self, exc: Exception) -> bool:
        return is_retryable_model_failure(exc)

    def build_error_meta(self, exc: Exception) -> Dict[str, Any]:
        meta = super().build_error_meta(exc)
        meta.update(
            {
                "classification": str(getattr(exc, "classification", "") or "").strip(),
                "code": str(getattr(exc, "code", "") or "").strip(),
                "status": getattr(exc, "status", None),
                "request_id": str(getattr(exc, "request_id", "") or "").strip(),
            }
        )
        return meta
