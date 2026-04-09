from __future__ import annotations

import re
from typing import Any

from django.db import transaction
from django.utils import timezone

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry


logger = get_app_logger("finance")

SOURCE_SUMMARY_STATUS_QUEUED = "queued"
SOURCE_SUMMARY_STATUS_RUNNING = "running"
SOURCE_SUMMARY_STATUS_READY = "ready"
SOURCE_SUMMARY_STATUS_ERROR = "error"
SOURCE_SUMMARY_MAX_LINES = 6


def build_source_summary_prompt(
    *,
    source_url: str,
    source_title: str = "",
    source_kind: str = "source",
    summary_lines: int = SOURCE_SUMMARY_MAX_LINES,
) -> str:
    title = str(source_title or "").strip() or "Linked source"
    kind = str(source_kind or "source").strip() or "source"
    url = str(source_url or "").strip()
    lines = max(5, min(int(summary_lines or SOURCE_SUMMARY_MAX_LINES), SOURCE_SUMMARY_MAX_LINES))
    return "\n".join(
        [
            "You are summarizing a linked source for a finance research card.",
            f"Source kind: {kind}",
            f"Source title: {title}",
            f"Source URL: {url}",
            f"Write exactly {lines} short lines of plain text.",
            "Focus on the material facts, dates, actions, and investor relevance.",
            "Do not invent facts that are not present in the source.",
            "If the source cannot be fetched, say so briefly and explain why.",
        ]
    )


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _split_summary_lines(value: object, *, max_lines: int = SOURCE_SUMMARY_MAX_LINES) -> list[str]:
    text = _normalize_text(value)
    if not text:
        return []
    text = text.replace("\r", "\n")
    raw_lines = [line.strip(" -*\t") for line in text.split("\n") if line.strip()]
    if len(raw_lines) >= 2:
        return raw_lines[:max_lines]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) >= 2:
        return sentences[:max_lines]
    if len(text) <= 120:
        return [text]
    chunks: list[str] = []
    words = text.split()
    current: list[str] = []
    current_len = 0
    for word in words:
        next_len = current_len + len(word) + (1 if current else 0)
        if current and next_len > 90:
            chunks.append(" ".join(current))
            if len(chunks) >= max_lines:
                break
            current = [word]
            current_len = len(word)
            continue
        current.append(word)
        current_len = next_len
    if current and len(chunks) < max_lines:
        chunks.append(" ".join(current))
    return chunks[:max_lines]


def _placeholder_lines(*, source_title: str, status: str) -> list[str]:
    title = str(source_title or "Linked source").strip()
    if status == SOURCE_SUMMARY_STATUS_ERROR:
        return [
            f"AI summary could not complete for {title}.",
            "The button can be used again to retry the fetch.",
            "If the page is paywalled or blocked, the fallback will report that.",
            "Once a successful run returns, the summary will replace these lines.",
        ]
    if status in {SOURCE_SUMMARY_STATUS_QUEUED, SOURCE_SUMMARY_STATUS_RUNNING}:
        return [
            f"Fetching {title} now.",
            "The finance agent is loading the linked page in the background.",
            "This card will update when the headless run returns.",
            "The spinner stays visible while the source is being processed.",
        ]
    return [
        f"Click the AI button to summarize {title}.",
        "The summary will appear here after the headless run finishes.",
        "Five to six lines are reserved for the returned note.",
        "This button can be reused on any linked research card.",
    ]


def _get_summary_map(payload: dict[str, Any]) -> dict[str, Any]:
    summary_map = payload.get("ai_summaries")
    return dict(summary_map) if isinstance(summary_map, dict) else {}


def _set_summary_state(
    entry: FinanceDataCacheEntry,
    *,
    source_url: str,
    state: dict[str, Any],
) -> FinanceDataCacheEntry:
    payload = dict(entry.payload or {})
    summary_map = _get_summary_map(payload)
    summary_map[str(source_url)] = state
    payload["ai_summaries"] = summary_map
    entry.payload = payload
    entry.save(update_fields=["payload", "updated_at"])
    return entry


def _workspace_entry_queryset(*, cache_key: str, workspace_id: str | None = None):
    queryset = FinanceDataCacheEntry.objects.select_for_update().filter(cache_key=cache_key)
    if workspace_id:
        queryset = queryset.filter(workspace_id=workspace_id)
    return queryset


@transaction.atomic
def mark_source_summary_queued(
    *,
    workspace_id: str | None = None,
    cache_key: str,
    source_url: str,
    source_title: str,
    source_kind: str,
    task_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    entry = _workspace_entry_queryset(cache_key=cache_key, workspace_id=workspace_id).first()
    if entry is None:
        logger.warning("source summary queue skipped missing cache_key=%s workspace_id=%s url=%s", cache_key, workspace_id or "", source_url)
        return {"ok": False, "status": "missing_cache_entry", "cache_key": cache_key}

    state = {
        "status": SOURCE_SUMMARY_STATUS_QUEUED,
        "source_url": str(source_url or "").strip(),
        "source_title": str(source_title or "").strip(),
        "source_kind": str(source_kind or "source").strip() or "source",
        "task_id": str(task_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "summary_text": "",
        "summary_lines": _placeholder_lines(source_title=source_title, status=SOURCE_SUMMARY_STATUS_QUEUED),
        "error": "",
        "updated_at": timezone.now().isoformat(),
    }
    _set_summary_state(entry, source_url=source_url, state=state)
    return {"ok": True, "status": SOURCE_SUMMARY_STATUS_QUEUED, "cache_key": cache_key, "state": state}


@transaction.atomic
def mark_source_summary_running(
    *,
    workspace_id: str | None = None,
    cache_key: str,
    source_url: str,
    source_title: str,
    source_kind: str,
    task_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    entry = _workspace_entry_queryset(cache_key=cache_key, workspace_id=workspace_id).first()
    if entry is None:
        logger.warning("source summary running skipped missing cache_key=%s workspace_id=%s url=%s", cache_key, workspace_id or "", source_url)
        return {"ok": False, "status": "missing_cache_entry", "cache_key": cache_key}

    state = {
        "status": SOURCE_SUMMARY_STATUS_RUNNING,
        "source_url": str(source_url or "").strip(),
        "source_title": str(source_title or "").strip(),
        "source_kind": str(source_kind or "source").strip() or "source",
        "task_id": str(task_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "summary_text": "",
        "summary_lines": _placeholder_lines(source_title=source_title, status=SOURCE_SUMMARY_STATUS_RUNNING),
        "error": "",
        "updated_at": timezone.now().isoformat(),
    }
    _set_summary_state(entry, source_url=source_url, state=state)
    return {"ok": True, "status": SOURCE_SUMMARY_STATUS_RUNNING, "cache_key": cache_key, "state": state}


@transaction.atomic
def store_source_summary_result(
    *,
    workspace_id: str | None = None,
    cache_key: str,
    source_url: str,
    source_title: str,
    source_kind: str,
    summary_text: str,
    run_id: str = "",
    task_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    entry = _workspace_entry_queryset(cache_key=cache_key, workspace_id=workspace_id).first()
    if entry is None:
        logger.warning("source summary result skipped missing cache_key=%s workspace_id=%s url=%s", cache_key, workspace_id or "", source_url)
        return {"ok": False, "status": "missing_cache_entry", "cache_key": cache_key}

    cleaned_text = _normalize_text(summary_text or error)
    summary_lines = _split_summary_lines(cleaned_text)
    if not summary_lines:
        summary_lines = _placeholder_lines(source_title=source_title, status=SOURCE_SUMMARY_STATUS_ERROR)
    state = {
        "status": SOURCE_SUMMARY_STATUS_READY if not error else SOURCE_SUMMARY_STATUS_ERROR,
        "source_url": str(source_url or "").strip(),
        "source_title": str(source_title or "").strip(),
        "source_kind": str(source_kind or "source").strip() or "source",
        "task_id": str(task_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "summary_text": cleaned_text,
        "summary_lines": summary_lines,
        "error": _normalize_text(error),
        "updated_at": timezone.now().isoformat(),
    }
    _set_summary_state(entry, source_url=source_url, state=state)
    return {"ok": True, "status": state["status"], "cache_key": cache_key, "state": state}


def extract_source_summary_state(cache_entry: FinanceDataCacheEntry | None, source_url: str) -> dict[str, Any] | None:
    if cache_entry is None:
        return None
    payload = cache_entry.payload if isinstance(cache_entry.payload, dict) else {}
    summary_map = _get_summary_map(payload)
    state = summary_map.get(str(source_url or "").strip())
    return dict(state) if isinstance(state, dict) else None
