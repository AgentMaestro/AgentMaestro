# backend/runs/services/state.py
from __future__ import annotations

from django.db import transaction

from core.services.limits import LimitKey, QUOTA_MANAGER
from runs.models import AgentRun
from runs.services.events import append_event


_LEGAL_RUN_TRANSITIONS = {
    AgentRun.Status.PENDING: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.CANCELED,
        AgentRun.Status.FAILED,
        AgentRun.Status.WAITING_FOR_SUBRUN,
    },
    AgentRun.Status.RUNNING: {
        AgentRun.Status.COMPLETED,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
        AgentRun.Status.WAITING_FOR_APPROVAL,
        AgentRun.Status.WAITING_FOR_TOOL,
        AgentRun.Status.WAITING_FOR_SUBRUN,
        AgentRun.Status.WAITING_FOR_USER,
        AgentRun.Status.PAUSED,
    },
    AgentRun.Status.PAUSED: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
    },
    AgentRun.Status.WAITING_FOR_APPROVAL: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
    },
    AgentRun.Status.WAITING_FOR_TOOL: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
    },
    AgentRun.Status.WAITING_FOR_SUBRUN: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
    },
    AgentRun.Status.WAITING_FOR_USER: {
        AgentRun.Status.RUNNING,
        AgentRun.Status.FAILED,
        AgentRun.Status.CANCELED,
    },
}

FINAL_RUN_STATUSES = {
    AgentRun.Status.COMPLETED,
    AgentRun.Status.FAILED,
    AgentRun.Status.CANCELED,
}


@transaction.atomic
def transition_run(*, run_id: str, new_status: str) -> AgentRun:
    """
    Transition a run to a new status while recording the state change event.
    """
    if new_status not in AgentRun.Status.values:
        raise ValueError(f"{new_status} is not a valid AgentRun status.")

    run = AgentRun.objects.select_for_update().get(id=run_id)
    current_status = run.status
    if current_status == new_status:
        return run

    allowed = _LEGAL_RUN_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ValueError(f"Illegal transition {current_status} -> {new_status}")

    run.status = new_status
    run.save(update_fields=["status", "updated_at"])

    append_event(
        run_id=run_id,
        event_type="state_changed",
        payload={"from": current_status, "to": new_status},
        correlation_id=run.correlation_id,
    )

    if new_status in FINAL_RUN_STATUSES:
        QUOTA_MANAGER.release_run_slots(
            str(run.workspace_id), str(run.id), include_parent=run.parent_run_id is None
        )

    return run
