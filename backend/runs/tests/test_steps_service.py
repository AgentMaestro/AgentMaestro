# backend/runs/tests/test_steps_service.py
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from runs.services.steps import append_step


def _make_run(name_suffix: str):
    User = get_user_model()
    user = User.objects.create_user(username=f"steps{name_suffix}", password="x")
    ws = Workspace.objects.create(name=f"Steps WS {name_suffix}")
    WorkspaceMembership.objects.create(workspace=ws, user=user, role=WorkspaceMembership.Role.OWNER)

    agent = Agent.objects.create(
        workspace=ws,
        name=f"Steps Agent {name_suffix}",
        system_prompt="You are a test agent.",
        created_by=user,
    )

    run = AgentRun.objects.create(
        workspace=ws,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="hello",
    )
    return run


@pytest.mark.django_db(transaction=True)
def test_append_step_increments_sequential_index():
    run = _make_run("inc")
    step1 = append_step(run_id=str(run.id), kind=AgentStep.Kind.PLAN, payload={"foo": "bar"})
    step2 = append_step(run_id=str(run.id), kind=AgentStep.Kind.MESSAGE, payload={"hi": True})

    run.refresh_from_db()
    assert step1.step_index == 1
    assert step2.step_index == 2
    assert run.current_step_index == 2


@pytest.mark.django_db(transaction=True)
def test_append_step_raises_on_duplicate_index():
    run = _make_run("dupe")
    AgentStep.objects.create(
        run=run,
        step_index=1,
        kind=AgentStep.Kind.OBSERVATION,
        payload={"existing": True},
    )

    run.refresh_from_db()
    run.current_step_index = 0
    run.save(update_fields=["current_step_index", "updated_at"])

    with pytest.raises(IntegrityError):
        append_step(run_id=str(run.id), kind=AgentStep.Kind.PLAN, payload={"foo": "bar"})
