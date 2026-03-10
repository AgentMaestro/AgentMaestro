from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_input_items(
    history: list[dict[str, object]],
    *,
    previous_response_id: str | None = None,
    outstanding_provider_call_id: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for entry in history:
        role = entry.get("role")
        content = (entry.get("content") or "").strip()
        if not content:
            continue

        if role == "tool":
            tool_call_id = str(entry.get("tool_call_id") or "").strip()
            provider_call_id = str(entry.get("provider_call_id") or "").strip()
            outstanding = bool(
                previous_response_id
                and provider_call_id
                and outstanding_provider_call_id
                and provider_call_id == outstanding_provider_call_id
            )
            logger.info(
                "Preparing tool output for provider run=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s outstanding=%s",
                run_id,
                tool_call_id or "<missing>",
                provider_call_id or "<missing>",
                previous_response_id,
                outstanding,
            )
            if not previous_response_id:
                logger.warning(
                    "Skipping tool output without previous_response_id run=%s tool_call_id=%s provider_call_id=%s",
                    run_id,
                    tool_call_id or "<missing>",
                    provider_call_id or "<missing>",
                )
                continue
            if not provider_call_id:
                logger.warning(
                    "Skipping tool output without provider_call_id run=%s tool_call_id=%s previous_response_id=%s",
                    run_id,
                    tool_call_id or "<missing>",
                    previous_response_id,
                )
                continue
            if not outstanding:
                logger.warning(
                    "Skipping tool output without matching outstanding provider call run=%s tool_call_id=%s provider_call_id=%s outstanding_provider_call_id=%s previous_response_id=%s",
                    run_id,
                    tool_call_id or "<missing>",
                    provider_call_id or "<missing>",
                    outstanding_provider_call_id or "<missing>",
                    previous_response_id,
                )
                continue
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": provider_call_id,
                    "output": content,
                }
            )
            continue

        if role not in {"system", "user", "assistant"}:
            continue

        if role == "system":
            item_role = "system"
        else:
            item_role = role

        content_item = (
            {"type": "output_text", "text": content}
            if role == "assistant"
            else {"type": "input_text", "text": content}
        )
        items.append(
            {
                "type": "message",
                "role": item_role,
                "content": [content_item],
            }
        )
    return items
