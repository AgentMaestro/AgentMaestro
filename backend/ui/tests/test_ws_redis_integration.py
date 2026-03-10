from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
import pytest

from agentmaestro.asgi import application
from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from django.contrib.auth import get_user_model
from django.test import Client
from runs.models import AgentRun


pytestmark = [pytest.mark.asyncio, pytest.mark.django_db]


def _require_redis_layer():
    channel_layer = get_channel_layer()
    if channel_layer is None or channel_layer.__class__.__name__ != "RedisChannelLayer":
        pytest.skip("RedisChannelLayer required for this integration test.")


def _session_cookie_for_user(user):
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


def _create_authenticated_run(run_id: str):
    workspace = Workspace.objects.create(name=f"RunGroup-{run_id}")
    user = get_user_model().objects.create_user(username=f"wsuser-{run_id}", password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name=f"RunAgent-{run_id}",
        soul="Test agent",
        created_by=user,
    )
    run = AgentRun.objects.create(
        id=run_id,
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="Integration test run",
    )
    return user, run


def _create_authenticated_workspace(workspace_id: str):
    workspace = Workspace.objects.create(id=workspace_id, name=f"WS-{workspace_id}")
    user = get_user_model().objects.create_user(username=f"wsuser-{workspace_id}", password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OPERATOR)
    return user, workspace


async def test_run_group_send_over_redis():
    _require_redis_layer()

    run_id = "22222222-2222-2222-2222-222222222222"
    ws_url = f"/ws/ui/run/{run_id}/"
    user, _ = _create_authenticated_run(run_id)
    sessionid = _session_cookie_for_user(user)
    communicator = WebsocketCommunicator(
        application,
        ws_url,
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )

    connected, _ = await communicator.connect()
    assert connected is True

    # Drain initial connected message
    _ = await communicator.receive_json_from()

    # Now push into the run group via channel layer
    channel_layer = get_channel_layer()
    assert channel_layer is not None

    payload = {
        "type": "push",
        "payload": {
            "type": "push",
            "topic": "run.event",
            "ts": "TEST",
            "event": "redis_group_send_ok",
            "data": {"hello": "redis"},
            "run_id": run_id,
        },
    }

    await channel_layer.group_send(f"run.{run_id}", payload)

    msg = await communicator.receive_json_from()
    assert msg["event"] == "redis_group_send_ok"
    assert msg["data"]["hello"] == "redis"
    assert msg["run_id"] == run_id

    await communicator.disconnect()


async def test_workspace_group_send_over_redis():
    _require_redis_layer()

    workspace_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ws_url = f"/ws/ui/workspace/?workspace_id={workspace_id}"
    user, _ = _create_authenticated_workspace(workspace_id)
    sessionid = _session_cookie_for_user(user)
    communicator = WebsocketCommunicator(
        application,
        ws_url,
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )

    connected, _ = await communicator.connect()
    assert connected is True

    # Drain initial connected message
    _ = await communicator.receive_json_from()

    channel_layer = get_channel_layer()

    await channel_layer.group_send(
        f"ws.{workspace_id}",
        {
            "type": "push",
            "payload": {
                "type": "push",
                "topic": "workspace.event",
                "ts": "TEST",
                "event": "redis_workspace_ok",
                "data": {"ok": True},
                "workspace_id": workspace_id,
            },
        },
    )

    msg = await communicator.receive_json_from()
    assert msg["event"] == "redis_workspace_ok"
    assert msg["workspace_id"] == workspace_id
    assert msg["data"]["ok"] is True

    await communicator.disconnect()
