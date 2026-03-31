import os
from typing import Any, Dict, List, Optional, Sequence


def env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# Exported debug flag
OPENAI_WS_DEBUG = env_flag("OPENAI_WS_DEBUG", "0")


class BaseLLMClient:
    """
    Base interface for LLM providers.
    """

    provider_name = "base"
    transport = "http"

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        reasoning: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        outstanding_provider_call_id: Optional[str] = None,
        outstanding_provider_call_ids: Optional[Sequence[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a chat completion and return a normalized response:
        {
            "text": str,
            "tool_calls": [ { "id": str, "name": str, "arguments": dict } ],
            "usage": { "prompt_tokens": int, "completion_tokens": int, "total_tokens": int },
            "raw": provider_response,
        }
        """
        raise NotImplementedError

    async def stream_text(self, *args: Any, **kwargs: Any):
        """
        Optional streaming hook. Implementations may yield text deltas.
        """
        raise NotImplementedError

    def preferred_transport(self) -> str:
        return str(getattr(self, "transport", "http") or "http").lower()

    def supports_ws_transport(self) -> bool:
        return False

    def resolve_transport(self) -> str:
        transport = self.preferred_transport()
        if transport == "ws" and not self.supports_ws_transport():
            return "http"
        return transport

    def is_transient_error(self, exc: Exception) -> bool:
        return False

    def is_ws_exception(self, exc: Exception) -> bool:
        return False

    def is_previous_response_not_found(self, exc: Exception) -> bool:
        return False

    def build_error_meta(self, exc: Exception) -> Dict[str, Any]:
        return {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
