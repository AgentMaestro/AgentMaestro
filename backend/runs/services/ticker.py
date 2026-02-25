from __future__ import annotations

from typing import Any, Dict

from django.db import transaction

from core.services.limits import LimitExceeded, LimitKey, QUOTA_MANAGER
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.recovery import (
    RunTickLocked,
    claim_run,
    is_cursor_at_expected,
    release_run_lock,
)
from runs.services.state import transition_run
from runs.services.steps import append_step
from runs.services.subruns import complete_subrun


STEP_CREATED_EVENT = "step_created"

MODEL_CALL_PAYLOAD = {"description": "Model call placeholder"}
OBSERVATION_PAYLOAD = {"description": "Observation placeholder"}


def _build_step_event_payload(step: AgentStep) -> Dict[str, Any]:
    return {
        "step_id": str(step.id),
        "step_index": step.step_index,
        "kind": step.kind,
        "payload": step.payload,
        "correlation_id": str(step.correlation_id),
    }


@transaction.atomic
def run_tick(*, run_id: str) -> Dict[str, Any]:
    """
    Deterministically advance a run through the MVP tick loop.

    - PENDING -> RUNNING (state_changed + MODEL_CALL + step_created event)
    - RUNNING -> COMPLETED (step_created + state_changed)
    - WAITING_FOR_APPROVAL just releases the lock and waits for human input.
    """
    run = None
    try:
        run = claim_run(run_id)

        try:
            QUOTA_MANAGER.record_request(str(run.workspace_id), LimitKey.RUN_TICK)
        except LimitExceeded as exc:
            raise RunTickLocked(f"Tick denied: {exc.limit.name}") from exc

        if run.status == AgentRun.Status.CANCELED:
            return {
                "run_id": run_id,
                "action": "cancelled",
                "status": run.status,
                "step_index": run.current_step_index,
            }

        if run.status == AgentRun.Status.PAUSED:
            return {
                "run_id": run_id,
                "action": "paused",
                "status": run.status,
                "step_index": run.current_step_index,
            }

        if run.status in {AgentRun.Status.COMPLETED, AgentRun.Status.FAILED}:
            return {
                "run_id": run_id,
                "action": "finalized",
                "status": run.status,
                "step_index": run.current_step_index,
            }

        if run.status == AgentRun.Status.PENDING:
            if not is_cursor_at_expected(run):
                return {
                    "run_id": run_id,
                    "action": "noop",
                    "status": run.status,
                    "step_index": run.current_step_index,
                }

            transition_run(run_id=run_id, new_status=AgentRun.Status.RUNNING)
            step = append_step(
                run_id=run_id,
                kind=AgentStep.Kind.MODEL_CALL,
                payload=MODEL_CALL_PAYLOAD,
            )
            append_event(
                run_id=run_id,
                event_type=STEP_CREATED_EVENT,
                payload=_build_step_event_payload(step),
                correlation_id=step.correlation_id,
            )
            return {
                "run_id": run_id,
                "action": "started_run",
                "status": AgentRun.Status.RUNNING,
                "step_index": step.step_index,
            }

        if run.status == AgentRun.Status.RUNNING:
            if not is_cursor_at_expected(run):
                return {
                    "run_id": run_id,
                    "action": "noop",
                    "status": run.status,
                    "step_index": run.current_step_index,
                }

            step = append_step(
                run_id=run_id,
                kind=AgentStep.Kind.OBSERVATION,
                payload=OBSERVATION_PAYLOAD,
            )
            append_event(
                run_id=run_id,
                event_type=STEP_CREATED_EVENT,
                payload=_build_step_event_payload(step),
                correlation_id=step.correlation_id,
            )
            transition_run(run_id=run_id, new_status=AgentRun.Status.COMPLETED)
            if run.parent_run_id:
                child_id = str(run.id)
                transaction.on_commit(lambda: complete_subrun(child_run_id=child_id))
            return {
                "run_id": run_id,
                "action": "completed_run",
                "status": AgentRun.Status.COMPLETED,
                "step_index": step.step_index,
            }

        if run.status == AgentRun.Status.WAITING_FOR_APPROVAL:
            return {
                "run_id": run_id,
                "action": "waiting_for_approval",
                "status": run.status,
                "step_index": run.current_step_index,
            }

        if run.status == AgentRun.Status.WAITING_FOR_SUBRUN:
            return {
                "run_id": run_id,
                "action": "waiting_for_subrun",
                "status": run.status,
                "step_index": run.current_step_index,
            }

        return {
            "run_id": run_id,
            "action": "noop",
            "status": run.status,
            "step_index": run.current_step_index,
        }
    finally:
        if run:
            release_run_lock(run)
