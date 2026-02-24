# backend/runs/services/ticker.py
from __future__ import annotations

from typing import Any, Dict

from django.db import DatabaseError, transaction

from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.state import transition_run
from runs.services.steps import append_step


class RunTickLocked(RuntimeError):
    """Raised when another ticker currently holds the lock for the run."""


MODEL_CALL_PAYLOAD = {"description": "Model call placeholder"}
OBSERVATION_PAYLOAD = {"description": "Observation placeholder"}


def _build_step_event_payload(step: AgentStep) -> Dict[str, Any]:
    return {
        "step_id": str(step.id),
        "step_index": step.step_index,
        "kind": step.kind,
        "payload": step.payload,
    }


@transaction.atomic
def run_tick(*, run_id: str) -> Dict[str, Any]:
    """
    Advance a run through the minimal tick loop using DB locks and events.

    First tick: PENDING -> RUNNING with MODEL_CALL step.
    Second tick: append OBSERVATION step and transition to COMPLETED.
    """
    try:
        run = AgentRun.objects.select_for_update(nowait=True).get(id=run_id)
    except DatabaseError as exc:
        raise RunTickLocked(f"Run {run_id} is locked") from exc

    if run.status == AgentRun.Status.PENDING:
        transition_run(run_id=run_id, new_status=AgentRun.Status.RUNNING)
        step = append_step(
            run_id=run_id,
            kind=AgentStep.Kind.MODEL_CALL,
            payload=MODEL_CALL_PAYLOAD,
        )
        append_event(
            run_id=run_id,
            event_type="step_appended",
            payload=_build_step_event_payload(step),
        )
        return {
            "run_id": run_id,
            "action": "started_run",
            "status": AgentRun.Status.RUNNING,
            "step_index": step.step_index,
        }

    if run.status == AgentRun.Status.RUNNING:
        step = append_step(
            run_id=run_id,
            kind=AgentStep.Kind.OBSERVATION,
            payload=OBSERVATION_PAYLOAD,
        )
        append_event(
            run_id=run_id,
            event_type="step_appended",
            payload=_build_step_event_payload(step),
        )
        transition_run(run_id=run_id, new_status=AgentRun.Status.COMPLETED)
        return {
            "run_id": run_id,
            "action": "completed_run",
            "status": AgentRun.Status.COMPLETED,
            "step_index": step.step_index,
        }

    return {
        "run_id": run_id,
        "action": "noop",
        "status": run.status,
        "step_index": run.current_step_index,
    }
