import asyncio

from agents.consumers import AgentChatConsumer


def test_flush_pending_tool_results(monkeypatch):
    consumer = AgentChatConsumer(scope={"type": "websocket"})
    consumer.channel_name = "test-channel"
    consumer.run_id = "run-123"
    sent = []

    async def fake_send(payload):
        sent.append(payload)

    consumer.send_json = fake_send

    expected_payload = {
        "tool_call_id": "call-1",
        "tool_name": "repo_tree",
        "status": "COMPLETED",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "result": {"ok": True},
        "duration_ms": 42,
        "run_id": "run-123",
        "correlation_id": "corr-1",
    }

    monkeypatch.setattr(
        "agents.consumers.pop_pending_tool_results",
        lambda run_id, limit=20: [dict(expected_payload)],
    )

    asyncio.run(consumer._flush_pending_tool_results())
    assert sent == [{"type": "tool_result", **expected_payload}]
