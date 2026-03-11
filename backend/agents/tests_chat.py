import pytest

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import Client
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentmaestro.asgi import application
from agents.consumers import AgentChatConsumer
from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from tools.models import AgentToolGrant, Tool, ToolDefinition, ToolGroup, ToolRisk


def _session_cookie_for_user(user):
    client = Client()
    client.force_login(user)
    return client.cookies["sessionid"].value


class DummySession:
    async def create_or_continue(self, *args, **kwargs):
        return {"text": "Assistant reply from stub"}

    async def close(self):
        pass


class DummyClient:
    def __init__(self):
        self.sent_tools = []
        self.session = DummySession()

    async def cleanup_ws_sessions(self):
        pass

    async def get_ws_session(self, run_id, model, *, agent_id=None):
        return self.session

    async def close_ws_session(self, run_id, *, model=None):
        pass

    def format_tool_definitions_for_responses(self, tools):
        self.sent_tools = tools or []
        return tools


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_chat_consumer_denies_non_member():
    workspace = Workspace.objects.create(name="chat-deny-ws")
    owner = get_user_model().objects.create_user(username="chat-owner")
    agent = Agent.objects.create(workspace=workspace, owner=owner, name="Chatty", soul="Hello", default_model="gpt-5")
    communicator = WebsocketCommunicator(application, f"/ws/agents/{agent.slug}/chat/")
    connected, _ = await communicator.connect()
    assert not connected
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_chat_consumer_sends_tools_and_messages(monkeypatch):
    workspace = Workspace.objects.create(name="chat-workspace")
    owner = get_user_model().objects.create_user(username="chat-owner-2")
    Agent.objects.create(workspace=workspace, owner=owner, name="Existing Agent", soul="Respond carefully.")
    agent = Agent.objects.get(name="Existing Agent")
    group = ToolGroup.objects.create(name="chat-group")
    tool = Tool.objects.create(
        name="chat_tool",
        tool_group=group,
        risk=ToolRisk.SAFE,
        requires_approval=False,
        released=True,
    )
    ToolDefinition.objects.create(workspace=workspace, tool=tool, enabled=True)
    AgentToolGrant.objects.create(agent=agent, tool=tool, enabled=True)
    WorkspaceMembership.objects.create(workspace=workspace, user=owner, role=WorkspaceMembership.Role.OPERATOR)

    dummy_client = DummyClient()
    monkeypatch.setattr("agents.consumers.get_client", lambda provider: dummy_client)

    sessionid = _session_cookie_for_user(owner)
    communicator = WebsocketCommunicator(
        application,
        f"/ws/agents/{agent.slug}/chat/",
        headers=[(b"cookie", f"sessionid={sessionid}".encode())],
    )
    connected, _ = await communicator.connect()
    assert connected
    connected_event = await communicator.receive_json_from()
    assert connected_event["type"] == "connected"
    assert "Respond carefully." in connected_event["system_context"]
    assert "read the repository `AGENTS.md` file" in connected_event["system_context"]
    assert "confirming that you read `AGENTS.md`" in connected_event["system_context"]
    assert "Repository instruction file:" in connected_event["system_context"]
    assert "Use this exact repo-root path when reading `AGENTS.md`" in connected_event["system_context"]
    assert any(tool["name"] == "chat_tool" for tool in connected_event["tools"])

    await communicator.send_json_to({"type": "chat.message", "text": "Hello world"})
    message = await communicator.receive_json_from()
    assert message["type"] == "message"
    assert message["text"] == "Assistant reply from stub"
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_agent_chat_consumer_parses_tool_args():
    user = get_user_model().objects.create_user(username="chat-parser")
    workspace = Workspace.objects.create(name="parser-ws")
    agent = Agent.objects.create(workspace=workspace, owner=user, name="Parser Agent", soul="Prompt")
    consumer = AgentChatConsumer(scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}})
    assert consumer._parse_tool_args("null") == {"raw": "null"}
    assert consumer._parse_tool_args('{"k":1}') == {"k": 1}
    assert consumer._parse_tool_args(None) == {}


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_agent_chat_consumer_push_sets_tool_call_future():
    user = get_user_model().objects.create_user(username="push-user")
    workspace = Workspace.objects.create(name="push-ws")
    agent = Agent.objects.create(workspace=workspace, owner=user, name="Push Agent", soul="Prompt")
    consumer = AgentChatConsumer(scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}})
    consumer.send_json = AsyncMock()
    future = asyncio.get_running_loop().create_future()
    consumer._tool_call_waiters = {"abc": future}
    await consumer.push(
        {
            "payload": {
                "event": "tool_call_completed",
                "data": {"tool_call_id": "abc", "result": {"ok": True}},
            }
        }
    )
    assert future.done()
    assert future.result()["tool_call_id"] == "abc"


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_build_ws_input_items_uses_only_tool_output_for_continuation():
    user = get_user_model().objects.create_user(username="ws-input-user")
    workspace = Workspace.objects.create(name="ws-input-ws")
    agent = Agent.objects.create(workspace=workspace, owner=user, name="WS Input Agent", soul="Prompt")
    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.run_id = "run-123"
    consumer.session = SimpleNamespace(previous_response_id="resp-123")
    consumer.history = [
        {"role": "system", "content": "system context"},
        {"role": "user", "content": "Hi, what do you know?"},
        {
            "role": "tool",
            "content": "{\"ok\": true}",
            "tool_call_id": "tool-123",
            "provider_call_id": "call-123",
        },
    ]
    consumer._tool_output_payload = {"provider_call_id": "call-123"}

    items = consumer._build_ws_input_items()

    assert items == [
        {
            "type": "function_call_output",
            "call_id": "call-123",
            "output": "{\"ok\": true}",
        }
    ]
