from __future__ import annotations

from typing import Any

from runs.models import AgentRun, RunEvent
from runs.services.events import append_event
from runs.services.memory import append_run_note, get_or_create_run_memory, update_run_memory

RUN_HANDOFF_EVENT = "run_handoff_received"
MAX_RECENT_EVENTS = 40
MAX_RECENT_MESSAGES = 3
MAX_RECENT_FAILURES = 4
MAX_KEY_FACTS = 8
MAX_OPEN_QUESTIONS = 6
MAX_TOOL_RESULTS = 4
MAX_NOTE_CHARS = 2000


def apply_successor_handoff(
    *,
    successor_run: AgentRun,
    predecessor_run: AgentRun,
    rotation_reason: str,
) -> dict[str, Any]:
    handoff = build_successor_handoff(
        predecessor_run=predecessor_run,
        rotation_reason=rotation_reason,
    )
    append_event(
        run_id=str(successor_run.id),
        event_type=RUN_HANDOFF_EVENT,
        payload=handoff,
        broadcast_to_run=False,
        correlation_id=successor_run.correlation_id,
    )

    successor_memory = get_or_create_run_memory(successor_run)
    predecessor_memory = get_or_create_run_memory(predecessor_run)
    update_run_memory(
        successor_run,
        objective=predecessor_memory.objective,
        current_plan=predecessor_memory.current_plan,
        key_facts=handoff.get("key_facts") or predecessor_memory.key_facts,
        open_questions=handoff.get("open_questions") or predecessor_memory.open_questions,
        recent_tool_results=handoff.get("recent_tool_results") or predecessor_memory.recent_tool_results,
        notes=_trim_text(build_handoff_note(handoff), MAX_NOTE_CHARS),
    )
    append_run_note(
        successor_run,
        f"Successor handoff applied from run {predecessor_run.id} because {handoff.get('rotation_reason_label') or 'the previous run ended unexpectedly'}.",
    )
    return handoff


def get_run_handoff_payload(run: AgentRun | str) -> dict[str, Any] | None:
    run_id = str(run.id if isinstance(run, AgentRun) else run)
    event = RunEvent.objects.filter(run_id=run_id, event_type=RUN_HANDOFF_EVENT).order_by("-seq").first()
    if not event or not isinstance(event.payload, dict):
        return None
    return dict(event.payload)


def build_handoff_system_note(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    lines = [
        "Automatic successor-run handoff:",
        "- Continue the prior work immediately.",
        "- Do not re-introduce yourself or restate AGENTS.md acknowledgement unless the user asks.",
    ]
    predecessor_run_id = str(payload.get("predecessor_run_id") or "").strip()
    if predecessor_run_id:
        lines.append(f"- Prior run id: {predecessor_run_id}")
    rotation_reason_label = str(payload.get("rotation_reason_label") or "").strip()
    if rotation_reason_label:
        lines.append(f"- Rotation reason: {rotation_reason_label}")
    predecessor_status = str(payload.get("predecessor_status") or "").strip()
    if predecessor_status:
        lines.append(f"- Prior run status: {predecessor_status}")
    predecessor_error_summary = str(payload.get("predecessor_error_summary") or "").strip()
    if predecessor_error_summary:
        lines.append(f"- Prior failure summary: {predecessor_error_summary}")
    objective = str(payload.get("objective") or "").strip()
    if objective:
        lines.append(f"- Current objective: {objective}")
    current_plan = str(payload.get("current_plan") or "").strip()
    if current_plan:
        lines.append(f"- Current plan: {current_plan}")

    recent_user_messages = payload.get("recent_user_messages") or []
    if recent_user_messages:
        lines.append("Recent user messages:")
        for message in recent_user_messages[:MAX_RECENT_MESSAGES]:
            lines.append(f"- {message}")

    key_facts = payload.get("key_facts") or []
    if key_facts:
        lines.append("Key facts to retain:")
        for fact in key_facts[:MAX_KEY_FACTS]:
            lines.append(f"- {fact}")

    open_questions = payload.get("open_questions") or []
    if open_questions:
        lines.append("Open questions:")
        for question in open_questions[:MAX_OPEN_QUESTIONS]:
            lines.append(f"- {question}")

    recent_tool_results = payload.get("recent_tool_results") or []
    if recent_tool_results:
        lines.append("Recent tool and subrun outcomes:")
        for entry in recent_tool_results[:MAX_TOOL_RESULTS]:
            tool_name = str(entry.get("tool_name") or "tool").strip()
            summary = str(entry.get("summary") or "").strip()
            if summary:
                lines.append(f"- {tool_name}: {summary}")

    recent_failure_events = payload.get("recent_failure_events") or []
    if recent_failure_events:
        lines.append("Recent abnormal events:")
        for entry in recent_failure_events[:MAX_RECENT_FAILURES]:
            lines.append(f"- {entry}")

    return "\n".join(lines)


def build_successor_handoff(*, predecessor_run: AgentRun, rotation_reason: str) -> dict[str, Any]:
    predecessor_memory = get_or_create_run_memory(predecessor_run)
    recent_events = list(
        RunEvent.objects.filter(run=predecessor_run).order_by("-seq")[:MAX_RECENT_EVENTS]
    )
    recent_user_messages = _recent_user_messages(recent_events)
    recent_failure_events = _recent_failure_events(recent_events)
    key_facts = _normalize_string_list(
        list(predecessor_memory.key_facts or [])
        + _derived_key_facts(predecessor_run, rotation_reason),
        MAX_KEY_FACTS,
    )
    open_questions = _normalize_string_list(predecessor_memory.open_questions or [], MAX_OPEN_QUESTIONS)
    recent_tool_results = _normalize_tool_results(
        list(predecessor_memory.recent_tool_results or [])
        + _derived_recent_tool_results(predecessor_run, recent_failure_events),
        MAX_TOOL_RESULTS,
    )
    return {
        "predecessor_run_id": str(predecessor_run.id),
        "rotation_reason": _normalize_rotation_reason(rotation_reason),
        "rotation_reason_label": _rotation_reason_label(rotation_reason),
        "predecessor_status": predecessor_run.status,
        "predecessor_error_summary": _trim_text(predecessor_run.error_summary, 600),
        "objective": _trim_text(predecessor_memory.objective, 600),
        "current_plan": _trim_text(predecessor_memory.current_plan, 600),
        "recent_user_messages": recent_user_messages,
        "key_facts": key_facts,
        "open_questions": open_questions,
        "recent_tool_results": recent_tool_results,
        "recent_failure_events": recent_failure_events,
        "notice": _build_notice(predecessor_run, rotation_reason),
    }


def build_handoff_note(payload: dict[str, Any]) -> str:
    note = build_handoff_system_note(payload)
    return _trim_text(note, MAX_NOTE_CHARS)


def _build_notice(predecessor_run: AgentRun, rotation_reason: str) -> str:
    label = _rotation_reason_label(rotation_reason)
    return f"Continuing work from run {predecessor_run.id} after {label}. Prior status: {predecessor_run.status.lower()}."


def _recent_user_messages(events: list[RunEvent]) -> list[str]:
    messages: list[str] = []
    for event in events:
        if event.event_type != "chat_message":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("role") or "").strip().lower() != "user":
            continue
        text = _trim_text(payload.get("text") or payload.get("content") or "", 280)
        if text:
            messages.append(text)
        if len(messages) >= MAX_RECENT_MESSAGES:
            break
    messages.reverse()
    return messages


def _recent_failure_events(events: list[RunEvent]) -> list[str]:
    failures: list[str] = []
    interesting_types = {
        "headless_run_failed",
        "tool_call_denied",
        "subrun_cancelled",
        "subrun_completed",
        "state_changed",
    }
    for event in events:
        if event.event_type not in interesting_types:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        summary = _summarize_failure_event(event.event_type, payload)
        if summary:
            failures.append(summary)
        if len(failures) >= MAX_RECENT_FAILURES:
            break
    failures.reverse()
    return failures


def _summarize_failure_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "headless_run_failed":
        return _trim_text(payload.get("summary") or payload.get("error") or "headless run failed", 240)
    if event_type == "tool_call_denied":
        tool_name = str(payload.get("tool_name") or "tool").strip()
        error = _trim_text(payload.get("error") or payload.get("message") or "denied", 180)
        return f"{tool_name} denied: {error}"
    if event_type == "subrun_cancelled":
        child_run_id = str(payload.get("child_run_id") or "child").strip()
        reason = _trim_text(payload.get("reason") or payload.get("child_status") or "cancelled", 180)
        return f"Subrun {child_run_id} cancelled: {reason}"
    if event_type == "subrun_completed":
        child_status = str(payload.get("child_status") or "").strip().upper()
        if child_status not in {AgentRun.Status.FAILED, AgentRun.Status.CANCELED}:
            return ""
        child_run_id = str(payload.get("child_run_id") or "child").strip()
        reason = _trim_text(payload.get("reason") or child_status.lower(), 180)
        return f"Subrun {child_run_id} finished with {child_status.lower()}: {reason}"
    if event_type == "state_changed":
        if str(payload.get("to") or "").strip().upper() not in {AgentRun.Status.FAILED, AgentRun.Status.WAITING_FOR_SUBRUN}:
            return ""
        return f"Run state changed to {str(payload.get('to') or '').strip().lower()}"
    return ""


def _derived_key_facts(predecessor_run: AgentRun, rotation_reason: str) -> list[str]:
    facts = [
        f"Prior run id: {predecessor_run.id}",
        f"Prior run status: {predecessor_run.status}",
        f"Rotation reason: {_rotation_reason_label(rotation_reason)}",
    ]
    error_summary = _trim_text(predecessor_run.error_summary, 240)
    if error_summary:
        facts.append(f"Prior failure summary: {error_summary}")
    input_text = _trim_text(predecessor_run.input_text, 240)
    if input_text:
        facts.append(f"Original run input: {input_text}")
    return facts


def _derived_recent_tool_results(predecessor_run: AgentRun, recent_failure_events: list[str]) -> list[dict[str, str]]:
    derived: list[dict[str, str]] = []
    for item in recent_failure_events[-MAX_TOOL_RESULTS:]:
        derived.append({"tool_name": "run_handoff", "summary": _trim_text(item, 280)})
    error_summary = _trim_text(predecessor_run.error_summary, 280)
    if error_summary:
        derived.append({"tool_name": "run_error", "summary": error_summary})
    return derived


def _normalize_rotation_reason(value: str) -> str:
    candidate = str(value or "unexpected_result").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {
        "failed_run",
        "waiting_for_subrun",
        "completed_run",
        "canceled_run",
        "unexpected_result",
    }
    return candidate if candidate in allowed else "unexpected_result"


def _rotation_reason_label(value: str) -> str:
    normalized = _normalize_rotation_reason(value)
    mapping = {
        "failed_run": "a failed run",
        "waiting_for_subrun": "a run waiting on a subrun",
        "completed_run": "a completed run",
        "canceled_run": "a canceled run",
        "unexpected_result": "an unexpected prior result",
    }
    return mapping.get(normalized, "an unexpected prior result")


def _normalize_string_list(values: list[Any], limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _trim_text(value, 280)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _normalize_tool_results(values: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        tool_name = _trim_text(value.get("tool_name") or "tool", 80)
        summary = _trim_text(value.get("summary") or "", 280)
        if not tool_name and not summary:
            continue
        normalized.append({"tool_name": tool_name or "tool", "summary": summary})
    return normalized[-limit:]


def _trim_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."
