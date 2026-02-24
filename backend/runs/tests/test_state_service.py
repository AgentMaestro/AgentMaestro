# backend/runs/tests/test_state_service.py
import pytest
from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, RunEvent
from runs.services.state import transition_run


def _make_run(name_suffix: str) -> AgentRun:
    User = get_user_model()
    user = User.objects.create_user(username=f"state{name_suffix}", password="x")
    ws = Workspace.objects.create(name=f"State WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=ws,
        name=f"State Agent {name_suffix}",
        system_prompt="State test agent.",
        created_by=user,
    )

    run = AgentRun.objects.create(
        workspace=ws,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="state",
    )
    return run


@pytest.mark.django_db(transaction=True)
def test_transition_run_allows_legal_transition():
    run = _make_run("legal")

    transition_run(run_id=str(run.id), new_status=AgentRun.Status.RUNNING)

    run.refresh_from_db()
    assert run.status == AgentRun.Status.RUNNING

    evt = RunEvent.objects.get(run=run, seq=1)
    assert evt.event_type == "state_changed"
    assert evt.payload == {"from": AgentRun.Status.PENDING, "to": AgentRun.Status.RUNNING}


@pytest.mark.django_db(transaction=True)
def test_transition_run_forbids_illegal_transition():
    run = _make_run("illegal")

    with pytest.raises(ValueError):
        transition_run(run_id=str(run.id), new_status=AgentRun.Status.COMPLETED)

    assert RunEvent.objects.filter(run=run).count() == 0
