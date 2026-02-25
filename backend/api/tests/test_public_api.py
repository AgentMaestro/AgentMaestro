import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.steps import append_step
from tools.services.approvals import request_tool_call_approval
from tools.models import ToolCall


@pytest.fixture
def factory_user():
    User = get_user_model()

    def _factory(username: str):
        return User.objects.create_user(username=username, password="x")

    return _factory


def _ensure_membership(workspace, user, role=WorkspaceMembership.Role.OWNER):
    WorkspaceMembership.objects.get_or_create(workspace=workspace, user=user, defaults={"role": role})


@pytest.mark.django_db
def test_start_run_api_creates_run(client, factory_user):
    user = factory_user("api-starter")
    workspace = Workspace.objects.create(name="API WS")
    _ensure_membership(workspace, user)
    agent = Agent.objects.create(workspace=workspace, name="API Agent", system_prompt="Prompt")

    client.force_login(user)
    payload = {
        "workspace_id": str(workspace.id),
        "agent_id": str(agent.id),
        "input_text": "api start",
    }

    with patch("runs.tasks.run_tick.delay") as mock_delay:
        response = client.post(
            reverse("api:start_run"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"]
    assert AgentRun.objects.filter(id=data["run_id"]).exists()
    mock_delay.assert_called_once()


@pytest.mark.django_db
def test_spawn_subrun_api_returns_child(client, factory_user):
    user = factory_user("api-spawner")
    workspace = Workspace.objects.create(name="Spawn WS")
    _ensure_membership(workspace, user)
    agent = Agent.objects.create(workspace=workspace, name="Spawn Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.RUNNING, input_text="parent")

    client.force_login(user)
    payload = {"input_text": "child run"}

    with patch("runs.tasks.run_tick.delay"):
        response = client.post(
            reverse("api:spawn_subrun", kwargs={"run_id": run.id}),
            data=json.dumps(payload),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = response.json()
    assert data["child_run_id"]
    assert data["status"] in {AgentRun.Status.PENDING, AgentRun.Status.RUNNING}


@pytest.mark.django_db
def test_approve_tool_call_api_transitions_run(client, factory_user):
    user = factory_user("api-approver")
    workspace = Workspace.objects.create(name="Approval WS")
    _ensure_membership(workspace, user)
    agent = Agent.objects.create(workspace=workspace, name="Approval Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.RUNNING, input_text="approval")

    tool_call = request_tool_call_approval(
        run_id=str(run.id),
        tool_name="search",
        args={"query": "test"},
        requires_approval=True,
    )

    client.force_login(user)
    with patch("runs.tasks.run_tick.delay") as mock_delay:
        response = client.post(
            reverse("api:approve_tool_call", kwargs={"tool_call_id": tool_call.id}),
            content_type="application/json",
        )

    assert response.status_code == 200
    tool_call.refresh_from_db()
    assert tool_call.status == ToolCall.Status.APPROVED
    mock_delay.assert_called_once_with(str(run.id))


@pytest.mark.django_db
def test_snapshot_api_filters_by_since_seq(client, factory_user):
    user = factory_user("api-snapshot")
    workspace = Workspace.objects.create(name="Snapshot WS")
    _ensure_membership(workspace, user)
    agent = Agent.objects.create(workspace=workspace, name="Snapshot Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(workspace=workspace, agent=agent, status=AgentRun.Status.PENDING, input_text="snapshot")

    append_step(run_id=str(run.id), kind=AgentStep.Kind.PLAN, payload={"plan": 1})
    append_event(run_id=str(run.id), event_type="state_changed", payload={"to": AgentRun.Status.RUNNING}, broadcast_to_run=False)

    client.force_login(user)
    response = client.get(reverse("api:run_snapshot", kwargs={"run_id": run.id}), {"since_seq": 1})

    assert response.status_code == 200
    data = response.json()
    assert data["run"]["id"] == str(run.id)
    assert all(evt["seq"] > 1 for evt in data["events_since_seq"])
