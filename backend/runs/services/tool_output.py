from __future__ import annotations

import json
from typing import Any

MAX_TOOL_OUTPUT_CONTEXT_CHARS = 5500
MAX_TOOL_OUTPUT_PREVIEW_ITEMS = 3
MAX_TOOL_OUTPUT_PREVIEW_TEXT = 240


def compact_tool_output_text(
    tool_name: str,
    payload: Any,
    *,
    max_chars: int = MAX_TOOL_OUTPUT_CONTEXT_CHARS,
) -> str:
    text = _stringify_payload(payload)
    if len(text) <= max_chars:
        return text

    tool_name = str(tool_name or "").strip().lower()
    if tool_name == "google_bridge":
        return _compact_google_bridge_output(payload, max_chars=max_chars)

    return _compact_generic_output(payload, max_chars=max_chars)


def _stringify_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(payload)


def _compact_generic_output(payload: Any, *, max_chars: int) -> str:
    if isinstance(payload, dict):
        summary: dict[str, Any] = {}
        if "ok" in payload:
            summary["ok"] = bool(payload.get("ok"))
        error_text = str(payload.get("error") or "").strip()
        if error_text:
            summary["error"] = _truncate_text(error_text, 600)
        result = payload.get("result")
        if result is not None:
            summary["result"] = _compact_value(result)
        meta = payload.get("meta")
        if meta:
            summary["meta"] = _compact_value(meta)
        text = _stringify_payload(summary)
    else:
        text = _stringify_payload(payload)
    return _truncate_output(text, max_chars=max_chars)


def _compact_google_bridge_output(payload: Any, *, max_chars: int) -> str:
    if not isinstance(payload, dict):
        return _truncate_output(_stringify_payload(payload), max_chars=max_chars)

    summary: dict[str, Any] = {
        "ok": bool(payload.get("ok", True)),
    }
    error_text = str(payload.get("error") or "").strip()
    if error_text:
        summary["error"] = _truncate_text(error_text, 600)

    result = payload.get("result")
    if isinstance(result, dict):
        summary["result"] = _compact_google_result(result)
    elif result is not None:
        summary["result"] = _compact_value(result)

    for key in ("integration_kind", "resource_kind", "action_kind", "operation", "summary_text"):
        value = payload.get(key)
        if value not in (None, ""):
            summary[key] = _truncate_text(str(value), 1000)

    steps = payload.get("steps")
    if isinstance(steps, list):
        summary["steps_count"] = len(steps)
        summary["steps_preview"] = [_compact_value(item) for item in steps[:MAX_TOOL_OUTPUT_PREVIEW_ITEMS]]

    accounts = payload.get("accounts")
    if isinstance(accounts, list):
        summary["accounts_count"] = len(accounts)
        summary["accounts_preview"] = [_compact_value(item) for item in accounts[:MAX_TOOL_OUTPUT_PREVIEW_ITEMS]]

    text = _stringify_payload(summary)
    return _truncate_output(text, max_chars=max_chars)


def _compact_google_result(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("summary_text", "query_plan", "shared_base_query", "execution_strategy"):
        value = result.get(key)
        if value not in (None, ""):
            summary[key] = _compact_value(value)

    for key in (
        "messages",
        "items",
        "files",
        "filters",
        "filter",
        "preview_filters",
        "calendars",
        "accounts",
        "connections",
        "results",
        "steps",
    ):
        value = result.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
            summary[f"{key}_preview"] = [_compact_value(item) for item in value[:MAX_TOOL_OUTPUT_PREVIEW_ITEMS]]
        elif value is not None:
            summary[key] = _compact_value(value)

    for key in (
        "resultSizeEstimate",
        "queued",
        "background",
        "child_run_id",
        "parent_run_id",
        "execution_mode",
        "nextPageToken",
        "nextSyncToken",
        "totalPeople",
        "totalItems",
    ):
        value = result.get(key)
        if value not in (None, ""):
            summary[key] = _compact_value(value)

    if not summary:
        summary["result"] = _compact_value(result)
    return summary


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compacted[key] = _compact_value(item)
        return compacted
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:MAX_TOOL_OUTPUT_PREVIEW_ITEMS]]
    if isinstance(value, tuple):
        return [_compact_value(item) for item in value[:MAX_TOOL_OUTPUT_PREVIEW_ITEMS]]
    if isinstance(value, str):
        return _truncate_text(value, MAX_TOOL_OUTPUT_PREVIEW_TEXT)
    return value


def _truncate_text(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _truncate_output(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max(0, max_chars - 3)].rstrip()}..."
