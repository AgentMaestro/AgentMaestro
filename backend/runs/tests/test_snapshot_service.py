# backend/runs/tests/test_snapshot_service.py
import pytest
from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.snapshot import get_run_snapshot
from runs.services.steps import append_step


def _make_run(name_suffix: str) -> AgentRun:
    User = get_user_model()
    user = User.objects.create_user(username=f"snap{name_suffix}", password="x")
    ws = Workspace.objects.create(name=f"Snapshot WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=ws,
        name=f"Snapshot Agent {name_suffix}",
        system_prompt="Snapshot test agent.",
        created_by=user,
    )

    run = AgentRun.objects.create(
        workspace=ws,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="snap",
    )
    return run


@pytest.mark.django_db(transaction=True)
def test_get_run_snapshot_initial_includes_steps_and_events():
    run = _make_run("initial")
    append_step(run_id=str(run.id), kind=AgentStep.Kind.PLAN, payload={"plan": 1})
    append_step(run_id=str(run.id), kind=AgentStep.Kind.MESSAGE, payload={"hello": True})

    append_event(run_id=str(run.id), event_type="state_changed", payload={"to": "RUNNING"}, broadcast_to_run=False)
    append_event(run_id=str(run.id), event_type="observation", payload={"ok": True}, broadcast_to_run=False)

    snapshot = get_run_snapshot(run_id=str(run.id), since_seq=None)

    assert snapshot["run"]["id"] == str(run.id)
    assert [step["step_index"] for step in snapshot["steps"]] == [1, 2]
    assert [evt["seq"] for evt in snapshot["events_since_seq"]] == [1, 2]


@pytest.mark.django_db(transaction=True)
def test_get_run_snapshot_since_seq_filters_events_only():
    run = _make_run("delta")
    append_step(run_id=str(run.id), kind=AgentStep.Kind.MODEL_CALL, payload={"model": "test"})

    append_event(run_id=str(run.id), event_type="first", payload={}, broadcast_to_run=False)
    append_event(run_id=str(run.id), event_type="second", payload={}, broadcast_to_run=False)
    append_event(run_id=str(run.id), event_type="third", payload={}, broadcast_to_run=False)

    snapshot = get_run_snapshot(run_id=str(run.id), since_seq=2)

    assert [evt["seq"] for evt in snapshot["events_since_seq"]] == [3]
    assert snapshot["steps"][0]["step_index"] == 1


@pytest.mark.django_db(transaction=True)
def test_get_run_snapshot_returns_empty_events_when_none_exist():
    run = _make_run("noevents")
    snapshot = get_run_snapshot(run_id=str(run.id))
    assert snapshot["events_since_seq"] == []
    assert snapshot["steps"] == []
