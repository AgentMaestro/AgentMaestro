from __future__ import annotations

from dataclasses import dataclass
import re

from agents.models import Agent
from memory.services import search_memory
from runs.models import AgentRun, RunEvent
from runs.services.events import append_event
from runs.services.memory import append_run_note

MEMORY_BOOTSTRAP_EVENT = "memory_bootstrap"
MAX_BOOTSTRAP_RESULTS = 5
MAX_QUERY_CHARS = 240


@dataclass(frozen=True)
class MemoryBootstrapResult:
    applied: bool
    query: str
    summary_text: str
    result_count: int


def bootstrap_memory_for_first_turn(run: AgentRun, agent: Agent, user_text: str) -> MemoryBootstrapResult | None:
    query = str(user_text or "").strip()
    if not _is_substantive(query):
        return None
    if RunEvent.objects.filter(run=run, event_type=MEMORY_BOOTSTRAP_EVENT).exists():
        return None

    query = query[:MAX_QUERY_CHARS].strip()
    matched_records = []
    seen_ids: set[str] = set()
    searched_scopes: list[dict[str, str]] = []

    query_variants = _query_variants(query)
    for scope_type, scope_id, scope_label in _candidate_scopes(run, agent):
        searched_scopes.append(
            {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "scope_label": scope_label,
            }
        )
        for query_variant in query_variants:
            for record in search_memory(
                query=query_variant,
                scope_type=scope_type,
                scope_id=scope_id,
                limit=3,
            ):
                record_id = str(record.id)
                if record_id in seen_ids:
                    continue
                seen_ids.add(record_id)
                matched_records.append(record)
                if len(matched_records) >= MAX_BOOTSTRAP_RESULTS:
                    break
            if len(matched_records) >= MAX_BOOTSTRAP_RESULTS:
                break
        if len(matched_records) >= MAX_BOOTSTRAP_RESULTS:
            break

    summary_text = _build_summary_text(matched_records)
    append_event(
        run_id=str(run.id),
        event_type=MEMORY_BOOTSTRAP_EVENT,
        payload={
            "query": query,
            "applied": bool(matched_records),
            "result_count": len(matched_records),
            "searched_scopes": searched_scopes,
            "results": [
                {
                    "memory_id": str(record.id),
                    "scope_type": record.scope_type,
                    "scope_id": record.scope_id,
                    "memory_kind": record.memory_kind,
                    "summary": record.summary,
                    "content_preview": record.content[:240],
                }
                for record in matched_records
            ],
        },
        broadcast_to_run=False,
    )
    if summary_text:
        append_run_note(run, f"memory bootstrap applied for query '{query}': {len(matched_records)} relevant record(s).")
    return MemoryBootstrapResult(
        applied=bool(matched_records),
        query=query,
        summary_text=summary_text,
        result_count=len(matched_records),
    )


def _candidate_scopes(run: AgentRun, agent: Agent) -> list[tuple[str, str, str]]:
    scopes: list[tuple[str, str, str]] = []
    if run.started_by_id:
        scopes.append(("user", str(run.started_by_id), "run_started_by_id"))
    if agent.workspace_id:
        scopes.append(("sandbox", str(agent.workspace_id), "workspace_id"))
    workspace_name = str(getattr(agent.workspace, "name", "") or "").strip()
    if workspace_name:
        scopes.append(("sandbox", workspace_name, "workspace_name"))
    scopes.append(("agent", str(agent.id), "agent_id"))
    agent_slug = str(getattr(agent, "slug", "") or "").strip()
    if agent_slug:
        scopes.append(("agent", agent_slug, "agent_slug"))
    return scopes


def _query_variants(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    variants = [normalized]
    seen = {normalized.casefold()}
    for token in re.findall(r"[a-zA-Z0-9_+-]+", normalized):
        candidate = token.strip().lower()
        if len(candidate) < 4:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        variants.append(candidate)
    return variants



def _is_substantive(text: str) -> bool:
    normalized = str(text or "").strip()
    if len(normalized) < 12:
        return False
    return len(normalized.split()) >= 3


def _build_summary_text(records: list) -> str:
    if not records:
        return ""
    lines = ["Relevant prior memory for this run:"]
    for record in records[:MAX_BOOTSTRAP_RESULTS]:
        summary = str(record.summary or "").strip() or str(record.content or "").strip()
        if len(summary) > 220:
            summary = summary[:219].rstrip() + "…"
        lines.append(f"- [{record.scope_type}/{record.memory_kind}] {summary}")
    return "\n".join(lines)
