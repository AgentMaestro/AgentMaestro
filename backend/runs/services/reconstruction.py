from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Iterable, List

from runs.models import RunEvent

logger = logging.getLogger(__name__)

RESUMABLE_EVENT_TYPES = {
    "chat_message",
    "assistant_message",
    "tool_call_requested",
    "tool_call_status",
    "tool_call_completed",
    "tool_call_denied",
}


def load_resumable_run_events(run_id: str) -> list[RunEvent]:
    events = list(RunEvent.objects.filter(run_id=run_id).order_by("seq"))
    counts = Counter(evt.event_type for evt in events)
    logger.info(
        "Loaded %s run events for run=%s types=%s",
        len(events),
        run_id,
        dict(counts),
    )
    return events


def filter_resumable_events(events: Iterable[RunEvent]) -> list[RunEvent]:
    filtered = [evt for evt in events if evt.event_type in RESUMABLE_EVENT_TYPES]
    counts = Counter(evt.event_type for evt in filtered)
    logger.debug(
        "Filtered resumable events count=%s types=%s",
        len(filtered),
        dict(counts),
    )
    return filtered


def build_provider_input_from_events(events: Iterable[RunEvent]) -> list[dict]:
    items: list[dict] = []
    for event in events:
        payload = event.payload or {}
        if event.event_type == "chat_message":
            role = str(payload.get("role") or "user")
            text = str(payload.get("text") or "").strip()
            if not text:
                logger.debug("Skipping empty chat_message event seq=%s", event.seq)
                continue
            items.append({"role": role, "content": text})
        elif event.event_type == "assistant_message":
            text = str(payload.get("content") or "").strip()
            role = str(payload.get("role") or "assistant")
            if not text:
                logger.debug("Skipping empty assistant_message event seq=%s", event.seq)
                continue
            items.append(
                {
                    "role": role,
                    "content": text,
                    "model": payload.get("model"),
                    "provider_response_id": payload.get("provider_response_id"),
                }
            )
        elif event.event_type == "tool_call_completed":
            tool_payload = payload.get("result") or {}
            serialized = _serialize_tool_output(tool_payload)
            items.append(
                {
                    "role": "tool",
                    "content": serialized,
                    "tool_call_id": payload.get("tool_call_id"),
                    "tool_name": payload.get("tool_name"),
                    "provider_call_id": payload.get("provider_call_id"),
                }
            )
        elif event.event_type == "tool_call_denied":
            summary = payload.get("error") or payload.get("message") or "denied"
            serialized = json.dumps({"error": summary}, ensure_ascii=False)
            items.append(
                {
                    "role": "tool",
                    "content": serialized,
                    "tool_call_id": payload.get("tool_call_id"),
                    "tool_name": payload.get("tool_name"),
                    "provider_call_id": payload.get("provider_call_id"),
                }
            )
        elif event.event_type == "tool_call_requested":
            # Preserve requested args for debugging or replay metadata.
            if payload.get("args"):
                items.append(
                    {
                        "role": "system",
                        "content": f"tool_call_requested:{payload.get('tool_call_id')}",
                    }
                )
        else:
            logger.debug("Skipping unsupported event_type=%s seq=%s", event.event_type, event.seq)
    return items


def _serialize_tool_output(payload: dict | None) -> str:
    if payload is None:
        return ""
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to serialize tool result payload=%s", payload)
        return str(payload)


def summarize_reconstruction(events: Iterable[RunEvent], items: Iterable[dict]) -> dict:
    event_list = list(events)
    item_list = list(items)
    event_counts = Counter(evt.event_type for evt in event_list)
    item_roles = Counter(item.get("role") for item in item_list if item.get("role"))
    summary = {
        "event_count": len(event_list),
        "item_count": len(item_list),
        "event_types": dict(event_counts),
        "item_roles": dict(item_roles),
    }
    logger.info("Reconstruction summary %s", summary)
    return summary
