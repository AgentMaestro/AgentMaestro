from __future__ import annotations

from typing import Any, Dict


def build_chat_message_payload(role: str, text: str) -> Dict[str, Any]:
    return {
        "role": role,
        "text": text,
    }


def build_assistant_message_payload(
    content: str,
    *,
    model: str | None = None,
    provider_response_id: str | None = None,
    step_index: int | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if model:
        payload["model"] = model
    if provider_response_id:
        payload["provider_response_id"] = provider_response_id
    if step_index is not None:
        payload["step_index"] = step_index
    return payload
