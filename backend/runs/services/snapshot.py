# backend/runs/services/snapshot.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from runs.models import AgentRun, AgentStep, RunEvent

RUN_FIELDS = [
    "id",
    "workspace_id",
    "agent_id",
    "parent_run_id",
    "started_by_id",
    "status",
    "channel",
    "input_text",
    "final_text",
    "current_step_index",
    "cancel_requested",
    "max_steps",
    "max_tool_calls",
    "locked_by",
    "lock_expires_at",
    "locked_at",
    "started_at",
    "ended_at",
    "error_summary",
    "correlation_id",
    "created_at",
    "updated_at",
]

STEP_FIELDS = [
    "id",
    "run_id",
    "step_index",
    "kind",
    "payload",
    "correlation_id",
    "created_at",
    "updated_at",
]
EVENT_FIELDS = [
    "id",
    "run_id",
    "seq",
    "event_type",
    "payload",
    "correlation_id",
    "created_at",
    "updated_at",
]
CHILD_RUN_FIELDS = [
    "id",
    "status",
    "started_at",
    "ended_at",
    "created_at",
    "agent_id",
    "current_step_index",
    "agent__name",
    "subrun_link__group_id",
    "subrun_link__join_policy",
    "subrun_link__quorum",
    "subrun_link__timeout_seconds",
    "subrun_link__failure_policy",
    "correlation_id",
]


def _serialize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _serialize_value(val) for key, val in record.items()}


def _serialize_queryset(queryset: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_serialize(record) for record in queryset]


def get_run_snapshot(run_id: str, since_seq: Optional[int] = None) -> Dict[str, Any]:
    """
    Return the canonical run state useful for replay/reconnect.

    Returns the latest `AgentRun` row, all steps (ordered by `step_index`),
    and any run events with `seq` greater than `since_seq`.
    """
    run_record = AgentRun.objects.filter(id=run_id).values(*RUN_FIELDS).first()
    if run_record is None:
        raise AgentRun.DoesNotExist(run_id)

    steps_qs = (
        AgentStep.objects.filter(run_id=run_id)
        .order_by("step_index")
        .values(*STEP_FIELDS)
    )

    events_qs = RunEvent.objects.filter(run_id=run_id)
    if since_seq is not None:
        events_qs = events_qs.filter(seq__gt=since_seq)
    events_qs = events_qs.order_by("seq").values(*EVENT_FIELDS)

    child_runs_qs = (
        AgentRun.objects.filter(parent_run_id=run_id)
        .order_by("created_at")
        .values(*CHILD_RUN_FIELDS)
    )

    return {
        "run": _serialize(run_record),
        "steps": _serialize_queryset(steps_qs),
        "events_since_seq": _serialize_queryset(events_qs),
        "child_runs": _serialize_queryset(child_runs_qs),
    }
