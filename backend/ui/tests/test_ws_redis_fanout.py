# backend/ui/tests/test_ws_redis_fanout.py
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
        pytest.skip("RedisChannelLayer required for this fanout test.")


def _session_cookie_for_user(user):
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


def _create_authenticated_run(run_id: str):
    workspace = Workspace.objects.create(name=f"RunFanout-{run_id}")
    user = get_user_model().objects.create_user(username=f"fanout-{run_id}", password="x")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.ADMIN)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name=f"FanoutAgent-{run_id}",
        soul="Fanout agent",
        created_by=user,
    )
    run = AgentRun.objects.create(
        id=run_id,
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.PENDING,
        input_text="Fanout run",
    )
    return user, run


async def test_run_group_fanout_two_clients():
    _require_redis_layer()

    run_id = "33333333-3333-3333-3333-333333333333"
    ws_url = f"/ws/ui/run/{run_id}/"

    user, _ = _create_authenticated_run(run_id)
    sessionid = _session_cookie_for_user(user)
    headers = [(b"cookie", f"sessionid={sessionid}".encode())]
    c1 = WebsocketCommunicator(application, ws_url, headers=headers)
    c2 = WebsocketCommunicator(application, ws_url, headers=headers)

    ok1, _ = await c1.connect()
    ok2, _ = await c2.connect()
    assert ok1 and ok2

    # Drain initial connected messages
    _ = await c1.receive_json_from()
    _ = await c2.receive_json_from()

    layer = get_channel_layer()
    await layer.group_send(
        f"run.{run_id}",
        {
            "type": "push",
            "payload": {
                "type": "push",
                "topic": "run.event",
                "ts": "TEST",
                "event": "fanout_ok",
                "data": {"n": 1},
                "run_id": run_id,
            },
        },
    )

    m1 = await c1.receive_json_from()
    m2 = await c2.receive_json_from()

    assert m1["event"] == "fanout_ok"
    assert m2["event"] == "fanout_ok"
    assert m1["data"]["n"] == 1
    assert m2["data"]["n"] == 1

    await c1.disconnect()
    await c2.disconnect()
