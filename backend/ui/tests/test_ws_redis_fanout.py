# backend/ui/tests/test_ws_redis_fanout.py
import os
import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from agentmaestro.asgi import application

pytestmark = pytest.mark.asyncio


def _skip_if_not_redis():
    if os.getenv("USE_REDIS_CHANNEL_LAYER") != "1":
        pytest.skip("Set USE_REDIS_CHANNEL_LAYER=1 to run Redis-backed Channels tests.")


async def test_run_group_fanout_two_clients():
    _skip_if_not_redis()

    run_id = "33333333-3333-3333-3333-333333333333"
    ws_url = f"/ws/ui/run/{run_id}/"

    c1 = WebsocketCommunicator(application, ws_url)
    c2 = WebsocketCommunicator(application, ws_url)

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