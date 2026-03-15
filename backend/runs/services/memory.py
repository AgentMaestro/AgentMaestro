from __future__ import annotations

import json
from typing import Iterable

from runs.models import AgentRun, RunMemory

MAX_FACTS = 25
MAX_OPEN_QUESTIONS = 25
MAX_RECENT_TOOL_RESULTS = 8
MAX_TOOL_SUMMARY_CHARS = 600
MAX_TEXT_FIELD_CHARS = 4000


def get_or_create_run_memory(run: AgentRun | str) -> RunMemory:
    run_id = str(run.id if isinstance(run, AgentRun) else run)
    memory, _created = RunMemory.objects.get_or_create(run_id=run_id)
    return memory


def update_run_memory(run: AgentRun | str, **fields) -> RunMemory:
    memory = get_or_create_run_memory(run)
    updated_fields: list[str] = []
    for field_name, value in fields.items():
        if not hasattr(memory, field_name):
            continue
        if field_name in {"objective", "current_plan", "notes"}:
            value = _trim_text(value, MAX_TEXT_FIELD_CHARS)
        elif field_name in {"key_facts", "open_questions"}:
            value = _normalize_string_list(value, MAX_FACTS if field_name == "key_facts" else MAX_OPEN_QUESTIONS)
        elif field_name == "recent_tool_results":
            value = _normalize_recent_tool_results(value)
        setattr(memory, field_name, value)
        updated_fields.append(field_name)
    if updated_fields:
        memory.save(update_fields=[*updated_fields, "updated_at"])
    return memory


def append_tool_result_summary(run: AgentRun | str, tool_name: str, summary: object) -> RunMemory:
    memory = get_or_create_run_memory(run)
    entries = list(memory.recent_tool_results or [])
    entries.append(
        {
            "tool_name": str(tool_name or "").strip(),
            "summary": _trim_text(summary, MAX_TOOL_SUMMARY_CHARS),
        }
    )
    memory.recent_tool_results = _normalize_recent_tool_results(entries)
    memory.save(update_fields=["recent_tool_results", "updated_at"])
    return memory


def merge_key_facts(run: AgentRun | str, facts: Iterable[object]) -> RunMemory:
    memory = get_or_create_run_memory(run)
    merged = list(memory.key_facts or [])
    merged.extend(_normalize_string_list(facts, MAX_FACTS))
    memory.key_facts = _dedupe_preserve_order(merged)[:MAX_FACTS]
    memory.save(update_fields=["key_facts", "updated_at"])
    return memory


def merge_open_questions(run: AgentRun | str, questions: Iterable[object]) -> RunMemory:
    memory = get_or_create_run_memory(run)
    merged = list(memory.open_questions or [])
    merged.extend(_normalize_string_list(questions, MAX_OPEN_QUESTIONS))
    memory.open_questions = _dedupe_preserve_order(merged)[:MAX_OPEN_QUESTIONS]
    memory.save(update_fields=["open_questions", "updated_at"])
    return memory


def append_run_note(run: AgentRun | str, note: object) -> RunMemory:
    memory = get_or_create_run_memory(run)
    addition = _trim_text(note, 1000)
    if not addition:
        return memory
    existing = _trim_text(memory.notes, MAX_TEXT_FIELD_CHARS)
    combined = addition if not existing else f"{existing}\n\n{addition}"
    memory.notes = _trim_text(combined, MAX_TEXT_FIELD_CHARS)
    memory.save(update_fields=["notes", "updated_at"])
    return memory


def _normalize_recent_tool_results(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        tool_name = _trim_text(item.get("tool_name"), 80)
        summary = _trim_text(item.get("summary"), MAX_TOOL_SUMMARY_CHARS)
        if not tool_name and not summary:
            continue
        normalized.append({"tool_name": tool_name, "summary": summary})
    return normalized[-MAX_RECENT_TOOL_RESULTS:]


def _normalize_string_list(values: object, limit: int) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized: list[str] = []
    for value in values:
        text = _trim_text(value, 500)
        if text:
            normalized.append(text)
    return _dedupe_preserve_order(normalized)[:limit]


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _trim_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = str(value)
        text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"
