from typing import Any, Dict, List, Optional, Sequence

from .base import BaseLLMClient


class OllamaClient(BaseLLMClient):
    """
    Placeholder client for a local Ollama deployment.
    """

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
        outstanding_provider_call_ids: Optional[Sequence[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "Ollama provider is not implemented yet. "
            "Provide a local completion endpoint and update this client accordingly."
        )

    async def stream_text(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("Ollama streaming is not implemented")
