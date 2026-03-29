from __future__ import annotations

from typing import Iterable

from logging_utils import get_app_logger
from runs.services.tool_output import compact_tool_output_text

logger = get_app_logger(__name__)


def build_input_items(
    history: list[dict[str, object]],
    *,
    previous_response_id: str | None = None,
    outstanding_provider_call_id: str | None = None,
    outstanding_provider_call_ids: Iterable[str] | None = None,
    run_id: str | None = None,
) -> list[dict[str, object]]:
    allowed_provider_call_ids: set[str] = set()
    if outstanding_provider_call_id:
        allowed_provider_call_ids.add(str(outstanding_provider_call_id).strip())
    if outstanding_provider_call_ids:
        for raw_value in outstanding_provider_call_ids:
            value = str(raw_value or "").strip()
            if value:
                allowed_provider_call_ids.add(value)

    items: list[dict[str, object]] = []
    for entry in history:
        role = entry.get("role")
        content = (entry.get("content") or "").strip()
        if not content:
            continue

        if role == "tool":
            tool_call_id = str(entry.get("tool_call_id") or "").strip()
            provider_call_id = str(entry.get("provider_call_id") or "").strip()
            tool_name = str(entry.get("tool_name") or "tool").strip() or "tool"
            content = compact_tool_output_text(tool_name, content)
            outstanding = bool(
                previous_response_id
                and provider_call_id
                and allowed_provider_call_ids
                and provider_call_id in allowed_provider_call_ids
            )
            logger.info(
                "Preparing tool output for provider run=%s tool_call_id=%s provider_call_id=%s previous_response_id=%s outstanding=%s allowed_provider_call_ids=%s",
                run_id,
                tool_call_id or "<missing>",
                provider_call_id or "<missing>",
                previous_response_id,
                outstanding,
                sorted(allowed_provider_call_ids),
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
                    "Skipping tool output without matching outstanding provider call run=%s tool_call_id=%s provider_call_id=%s outstanding_provider_call_ids=%s previous_response_id=%s",
                    run_id,
                    tool_call_id or "<missing>",
                    provider_call_id or "<missing>",
                    sorted(allowed_provider_call_ids),
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


def select_turn_history(
    history: list[dict[str, object]],
    *,
    previous_response_id: str | None = None,
    outstanding_provider_call_id: str | None = None,
    outstanding_provider_call_ids: Iterable[str] | None = None,
    run_id: str | None = None,
) -> list[dict[str, object]]:
    previous_response_id = str(previous_response_id or "").strip() or None
    if not previous_response_id:
        logger.info(
            "Selected turn history run=%s mode=full_history items=%d previous_response_id=<none>",
            run_id,
            len(history),
        )
        return list(history)

    allowed_provider_call_ids: set[str] = set()
    if outstanding_provider_call_id:
        allowed_provider_call_ids.add(str(outstanding_provider_call_id).strip())
    if outstanding_provider_call_ids:
        for raw_value in outstanding_provider_call_ids:
            value = str(raw_value or "").strip()
            if value:
                allowed_provider_call_ids.add(value)

    if history and history[-1].get("role") == "tool" and allowed_provider_call_ids:
        tool_entries: list[dict[str, object]] = []
        for entry in reversed(history):
            if entry.get("role") != "tool":
                break
            provider_call_id = str(entry.get("provider_call_id") or "").strip()
            if provider_call_id and provider_call_id in allowed_provider_call_ids:
                tool_entries.append(entry)
        tool_entries.reverse()
        if tool_entries:
            logger.info(
                "Selected turn history run=%s mode=tool_outputs_only items=%d previous_response_id=%s provider_call_ids=%s",
                run_id,
                len(tool_entries),
                previous_response_id,
                sorted(allowed_provider_call_ids),
            )
            return tool_entries

    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            selected = list(history[index:])
            logger.info(
                "Selected turn history run=%s mode=last_user_turn items=%d previous_response_id=%s",
                run_id,
                len(selected),
                previous_response_id,
            )
            return selected

    logger.info(
        "Selected turn history run=%s mode=empty_fallback items=0 previous_response_id=%s",
        run_id,
        previous_response_id,
    )
    return []


def build_ws_request_input_items(
    history: list[dict[str, object]],
    *,
    previous_response_id: str | None = None,
    outstanding_provider_call_id: str | None = None,
    outstanding_provider_call_ids: Iterable[str] | None = None,
    include_system_context: bool = True,
    last_user_text: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, object]]:
    previous_response_id = str(previous_response_id or "").strip() or None
    if not previous_response_id:
        items = build_input_items(history, run_id=run_id)
        logger.info(
            "Built WS request input items run=%s mode=initial_full_history items=%d previous_response_id=<none>",
            run_id,
            len(items),
        )
        return items

    if history and history[-1].get("role") == "tool":
        tool_entries: list[dict[str, object]] = []
        provider_call_ids: list[str] = []
        for entry in reversed(history):
            if entry.get("role") != "tool":
                break
            tool_entries.append(entry)
            provider_call_id = str(entry.get("provider_call_id") or entry.get("tool_call_id") or "").strip()
            if provider_call_id:
                provider_call_ids.append(provider_call_id)
        tool_entries.reverse()
        provider_call_ids.reverse()
        items = build_input_items(
            tool_entries,
            previous_response_id=previous_response_id,
            outstanding_provider_call_id=outstanding_provider_call_id,
            outstanding_provider_call_ids=(
                list(outstanding_provider_call_ids) if outstanding_provider_call_ids else provider_call_ids
            ),
            run_id=run_id,
        )
        logger.info(
            "Built WS request input items run=%s mode=tool_outputs_only items=%d previous_response_id=%s provider_call_ids=%s",
            run_id,
            len(items),
            previous_response_id,
            provider_call_ids,
        )
        return items

    if include_system_context:
        items = build_input_items(
            history,
            outstanding_provider_call_id=outstanding_provider_call_id,
            outstanding_provider_call_ids=outstanding_provider_call_ids,
            run_id=run_id,
        )
        logger.info(
            "Built WS request input items run=%s mode=full_history_with_continuity items=%d previous_response_id=%s",
            run_id,
            len(items),
            previous_response_id,
        )
        return items

    last_user_text = str(last_user_text or "").strip()
    if last_user_text:
        artifact_context_items: list[dict[str, object]] = []
        artifact_context_previews: list[str] = []
        for entry in history:
            if entry.get("role") != "system":
                continue
            if str(entry.get("kind") or "").strip() != "artifact_context":
                continue
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            artifact_context_previews.append(" ".join(content.split())[:240])
            artifact_context_item: dict[str, object] = {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": content}],
            }
            artifact_context_items.append(artifact_context_item)
        items = [
            *artifact_context_items,
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": last_user_text}],
            }
        ]
        logger.info(
            "Built WS request input items run=%s mode=last_user_only_with_artifact_context items=%d previous_response_id=%s artifact_context_items=%d artifact_context_preview=%s",
            run_id,
            len(items),
            previous_response_id,
            len(artifact_context_items),
            artifact_context_previews[:2],
        )
        return items

    items = build_input_items(
        history,
        outstanding_provider_call_id=outstanding_provider_call_id,
        outstanding_provider_call_ids=outstanding_provider_call_ids,
        run_id=run_id,
    )
    logger.info(
        "Built WS request input items run=%s mode=fallback_full_history items=%d previous_response_id=%s",
        run_id,
        len(items),
        previous_response_id,
    )
    return items
