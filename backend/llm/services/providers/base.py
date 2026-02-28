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
