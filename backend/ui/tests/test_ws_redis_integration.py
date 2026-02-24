# backend/ui/tests/test_ws_redis_integration.py
import os
import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from agentmaestro.asgi import application


pytestmark = pytest.mark.asyncio


def _skip_if_not_redis():
    if os.getenv("USE_REDIS_CHANNEL_LAYER") != "1":
        pytest.skip("Set USE_REDIS_CHANNEL_LAYER=1 to run Redis-backed Channels tests.")


async def test_run_group_send_over_redis():
    _skip_if_not_redis()

    run_id = "22222222-2222-2222-2222-222222222222"
    ws_url = f"/ws/ui/run/{run_id}/"
    communicator = WebsocketCommunicator(application, ws_url)

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
    _skip_if_not_redis()

    workspace_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ws_url = f"/ws/ui/workspace/?workspace_id={workspace_id}"
    communicator = WebsocketCommunicator(application, ws_url)

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