import json
from unittest.mock import patch

import pytest
from django.urls import reverse

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from django.contrib.auth import get_user_model
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event
from runs.services.steps import append_step


@pytest.mark.django_db
def test_dev_start_run_get_renders_button(client):
    resp = client.get(reverse("ui:dev_start_run"))
    assert resp.status_code == 200
    assert "Start Run" in resp.content.decode()


@pytest.mark.django_db
@patch("runs.tasks.run_tick.delay")
def test_dev_start_run_post_creates_run(mock_delay, client):
    resp = client.post(
        reverse("ui:dev_start_run"),
        data=json.dumps({"input_text": "Test prompt"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    payload = resp.json()
    run_id = payload["run_id"]
    assert AgentRun.objects.filter(id=run_id).exists()
    assert payload["run_url"].endswith(f"/ui/run/{run_id}/")
    mock_delay.assert_called_once_with(run_id)


@pytest.mark.django_db
def test_run_detail_page_displays_run(client):
    workspace = Workspace.objects.create(name="UI Test WS")
    agent = Agent.objects.create(workspace=workspace, name="UI Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        status=AgentRun.Status.PENDING,
        input_text="Test",
    )

    resp = client.get(reverse("ui:run_detail", kwargs={"run_id": run.id}))
    content = resp.content.decode()
    assert str(run.id) in content
    assert workspace.name in content
    assert agent.name in content


@pytest.mark.django_db
def test_run_snapshot_endpoint(client):
    workspace = Workspace.objects.create(name="Snapshot UI WS")
    agent = Agent.objects.create(workspace=workspace, name="Snapshot Agent", system_prompt="Prompt")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        status=AgentRun.Status.RUNNING,
        input_text="Snapshot test",
    )
    append_step(run_id=str(run.id), kind=AgentStep.Kind.MODEL_CALL, payload={"foo": "bar"})
    append_event(
        run_id=str(run.id),
        event_type="state_changed",
        payload={"to": AgentRun.Status.RUNNING},
        broadcast_to_run=False,
    )

    user = get_user_model().objects.create_user(username="snapshot-user", password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OPERATOR)
    client.force_login(user)

    resp = client.get(reverse("ui:run_snapshot", kwargs={"run_id": run.id}))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["run"]["id"] == str(run.id)
    assert payload["steps"][0]["kind"] == AgentStep.Kind.MODEL_CALL
    assert payload["events_since_seq"][-1]["event_type"] == "state_changed"
