import asyncio

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
from runs.models import AgentRun, RunEvent
from runs.services.events import append_event
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


class ResumeStubClient:
    def __init__(self, *, provider: str, transport: str, session_previous_response_id: str = ""):
        self.provider = provider
        self.transport = transport
        self.session = SimpleNamespace(previous_response_id=session_previous_response_id)
        self.cleanup_calls = 0
        self.get_session_calls: list[tuple[str, str, str | None]] = []

    def resolve_transport(self) -> str:
        return self.transport

    async def cleanup_ws_sessions(self):
        self.cleanup_calls += 1

    async def get_ws_session(self, run_id, model, *, agent_id=None):
        self.get_session_calls.append((run_id, model, agent_id))
        return self.session

    async def close_ws_session(self, run_id, *, model=None):
        return None

    def format_tool_definitions_for_responses(self, tools):
        return tools


RETRYABLE_STATUS_CODES = (408, 409, 429, 500, 502, 503, 504)


class FailoverStubClient:
    def __init__(
        self,
        *,
        provider: str,
        behavior_by_model: dict[str, list[dict[str, object]]],
        call_log: list[tuple[str, str]],
    ):
        self.provider = provider
        self.behavior_by_model = behavior_by_model
        self.call_log = call_log
        self.transport = "http"

    def resolve_transport(self) -> str:
        return "http"

    def is_transient_error(self, exc: Exception) -> bool:
        return int(getattr(exc, "status_code", 0) or 0) in RETRYABLE_STATUS_CODES

    async def complete(self, *args, **kwargs):
        model = str(kwargs.get("model") or "").strip()
        self.call_log.append((self.provider, model))
        queue = self.behavior_by_model.setdefault(model, [])
        if not queue:
            return {
                "text": f"{model} success",
                "tool_calls": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "raw": {"model": model},
                "response_id": f"resp-{model}",
            }
        action = queue.pop(0)
        if action["type"] == "error":
            raise RuntimeErrorWithStatus(int(action["status_code"]))
        return action["response"]


class RuntimeErrorWithStatus(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.status = status_code
        self.classification = "network_error"
        super().__init__(f"provider error {status_code}")


def _seed_consumer_backup_models() -> None:
    from llm.models import ModelsAvailable

    for name in ("gemini-2.5-flash", "gemini-2.5-pro"):
        ModelsAvailable.objects.get_or_create(
            company="google",
            api="gemini",
            name=name,
            defaults={"metadata": {}},
        )


def _build_failover_clients(call_log: list[tuple[str, str]], retry_same_model_attempts: int) -> dict[str, FailoverStubClient]:
    primary_model = "primary-model"
    backup_1 = "gemini-2.5-flash"
    backup_2 = "gemini-2.5-pro"
    error_queue = [{"type": "error", "status_code": 503} for _ in range(retry_same_model_attempts + 1)]
    return {
        "openai": FailoverStubClient(
            provider="openai",
            behavior_by_model={primary_model: list(error_queue)},
            call_log=call_log,
        ),
        "gemini": FailoverStubClient(
            provider="gemini",
            behavior_by_model={
                backup_1: list(error_queue),
                backup_2: [
                    {
                        "type": "success",
                        "response": {
                            "text": "final answer",
                            "tool_calls": [],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                            "raw": {"model": backup_2},
                            "response_id": "resp-final",
                        },
                    }
                ],
            },
            call_log=call_log,
        ),
    }


async def _noop_sleep(*args, **kwargs):
    return None


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
    Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="Existing Agent",
        description="Handles coding and operator workflows.",
        soul="Respond carefully.",
    )
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
    assert "Handles coding and operator workflows." in connected_event["system_context"]
    assert "Respond carefully." in connected_event["system_context"]
    assert "If this is the first turn of a new run and the repository `AGENTS.md` file is available" in connected_event["system_context"]
    assert "After the file has been read once in this run" in connected_event["system_context"]
    assert "Repository instruction file:" in connected_event["system_context"]
    assert "Use this exact repo-root path when calling `file_read`" in connected_event["system_context"]
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
async def test_agent_chat_consumer_blocks_dispatch_while_waiting_for_subrun():
    user = get_user_model().objects.create_user(username="waiting-subrun-user")
    workspace = Workspace.objects.create(name="waiting-subrun-ws")
    agent = Agent.objects.create(workspace=workspace, owner=user, name="Waiting Agent", soul="Prompt")
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.WAITING_FOR_SUBRUN,
        input_text="parent run",
    )
    consumer = AgentChatConsumer(scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}})
    consumer.run_id = str(run.id)

    assert await consumer._dispatch_blocked_by_run_status() is True


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


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_build_ws_input_items_resends_system_context_on_first_turn_after_connect():
    user = get_user_model().objects.create_user(username="ws-reconnect-user")
    workspace = Workspace.objects.create(name="ws-reconnect-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="Reconnect Agent",
        description="Preserve agent identity after reconnect.",
        soul="Always announce reconnect readiness.",
    )
    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.model_name = "gpt-5-codex"
    consumer.transport = "ws"
    consumer.run_id = "run-456"
    consumer.session = SimpleNamespace(previous_response_id="resp-456")
    consumer.history = [{"role": "user", "content": "who are you?"}]
    consumer._ensure_system_context()

    items = consumer._build_ws_input_items()

    assert items[0]["role"] == "system"
    assert "Preserve agent identity after reconnect." in items[0]["content"][0]["text"]
    assert "Always announce reconnect readiness." in items[0]["content"][0]["text"]
    assert items[-1]["role"] == "user"
    assert items[-1]["content"][0]["text"] == "who are you?"

@pytest.mark.django_db(transaction=True)
def test_ensure_system_context_switches_to_bootstrap_complete_after_agents_md_read():
    user = get_user_model().objects.create_user(username="ws-bootstrap-complete-user")
    workspace = Workspace.objects.create(name="ws-bootstrap-complete-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="Bootstrap Complete Agent",
        description="Keep the base system context first.",
        soul="Preserve relevant prior memory.",
    )
    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.model_name = "gpt-5-codex"
    consumer.transport = "ws"
    consumer.run_id = "run-bootstrap-complete"
    consumer.session = SimpleNamespace(previous_response_id="resp-bootstrap-complete")
    consumer.history = [{"role": "user", "content": "who are you?"}]

    consumer._ensure_system_context()
    assert "If this is the first turn of a new run and the repository `AGENTS.md` file is available" in consumer.system_context

    consumer._agents_md_bootstrap_complete = True
    consumer._ensure_system_context()

    assert "AGENTS.md has already been read in this run." in consumer.system_context
    assert "Do not call `file_read` on `AGENTS.md` again" in consumer.system_context
    assert "If this is the first turn of a new run and the repository `AGENTS.md` file is available" not in consumer.system_context


@pytest.mark.django_db(transaction=True)
def test_hydrate_agents_md_bootstrap_state_restores_marker_from_run_events():
    user = get_user_model().objects.create_user(username="ws-bootstrap-marker-user")
    workspace = Workspace.objects.create(name="ws-bootstrap-marker-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="Bootstrap Marker Agent",
        description="Keep the base system context first.",
        soul="Preserve relevant prior memory.",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="hello",
    )
    append_event(
        run_id=str(run.id),
        event_type="agents_md_bootstrap",
        payload={
            "tool_call_id": "tool-123",
            "tool_name": "file_read",
            "path": "C:\\Dev\\AgentMaestro\\AGENTS.md",
            "status": "COMPLETED",
        },
        broadcast_to_run=False,
    )

    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.run = run
    consumer.run_id = str(run.id)
    consumer.model_name = "gpt-5-codex"
    consumer.transport = "ws"
    consumer.session = SimpleNamespace(previous_response_id="")

    consumer._ensure_system_context()
    assert "If this is the first turn of a new run and the repository `AGENTS.md` file is available" in consumer.system_context

    asyncio.run(consumer._hydrate_agents_md_bootstrap_state())
    consumer._ensure_system_context()

    assert "AGENTS.md has already been read in this run." in consumer.system_context
    assert "Do not call `file_read` on `AGENTS.md` again" in consumer.system_context
    assert "If this is the first turn of a new run and the repository `AGENTS.md` file is available" not in consumer.system_context


@pytest.mark.django_db(transaction=True)
def test_build_ws_input_items_preserves_bootstrap_system_message_on_first_turn():
    user = get_user_model().objects.create_user(username="ws-bootstrap-user")
    workspace = Workspace.objects.create(name="ws-bootstrap-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="Bootstrap Agent",
        description="Keep the base system context first.",
        soul="Preserve relevant prior memory.",
    )
    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.model_name = "gpt-5-codex"
    consumer.transport = "ws"
    consumer.run_id = "run-bootstrap"
    consumer.session = SimpleNamespace(previous_response_id="")
    consumer.history = [
        {"role": "system", "content": "Relevant prior memory for this run:\n- [user/semantic] Ocala local time preference"},
        {"role": "user", "content": "What time is it in Ocala right now?"},
    ]

    consumer._ensure_system_context()
    items = consumer._build_ws_input_items()

    assert items[0]["role"] == "system"
    assert "Keep the base system context first." in items[0]["content"][0]["text"]
    assert items[1]["role"] == "system"
    assert "Relevant prior memory for this run:" in items[1]["content"][0]["text"]
    assert "Ocala local time preference" in items[1]["content"][0]["text"]
    assert items[2]["role"] == "user"
    assert items[2]["content"][0]["text"] == "What time is it in Ocala right now?"



@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_to_provider_http_stops_after_tool_call():
    user = get_user_model().objects.create_user(username="http-tool-loop-user")
    workspace = Workspace.objects.create(name="http-tool-loop-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="HTTP Loop Agent",
        soul="Reply carefully.",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="hi",
    )

    class ToolCallClient:
        def __init__(self):
            self.calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "file_read",
                        "arguments": '{"path": "C:\\Dev\\AgentMaestro\\AGENTS.md", "mode": "text"}',
                    }
                ],
                "response_id": "resp-1",
            }

    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.run = run
    consumer.run_id = str(run.id)
    consumer.model_name = "gpt-5.4-mini"
    consumer.transport = "http"
    consumer.use_ws = False
    consumer.session = SimpleNamespace(previous_response_id="")
    consumer.client = ToolCallClient()
    consumer.tool_definitions = [{"name": "file_read", "description": "", "parameters": {}}]
    consumer.history = [
        {"role": "system", "content": "Bootstrap status: pending"},
        {"role": "user", "content": "hi"},
    ]
    consumer.send_json = AsyncMock()
    consumer._handle_tool_call = AsyncMock()

    await consumer._dispatch_to_provider_http()

    assert consumer.client.calls == 1
    assert consumer.session.previous_response_id == "resp-1"
    consumer._handle_tool_call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_to_provider_http_clears_previous_response_after_final_answer():
    user = get_user_model().objects.create_user(username="http-clear-chain-user")
    workspace = Workspace.objects.create(name="http-clear-chain-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="HTTP Clear Chain Agent",
        soul="Reply carefully.",
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="hello",
    )

    class TextOnlyClient:
        def __init__(self):
            self.calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return {
                "text": "Hi!",
                "tool_calls": [],
                "response_id": "resp-final",
            }

    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.run = run
    consumer.run_id = str(run.id)
    consumer.model_name = "gpt-5.4-mini"
    consumer.transport = "http"
    consumer.use_ws = False
    consumer.session = SimpleNamespace(previous_response_id="resp-old")
    consumer.client = TextOnlyClient()
    consumer.tool_definitions = [{"name": "file_read", "description": "", "parameters": {}}]
    consumer.history = [
        {"role": "system", "content": "Bootstrap status: pending"},
        {"role": "user", "content": "hello"},
    ]
    consumer.send_json = AsyncMock()
    consumer._handle_tool_call = AsyncMock()

    await consumer._dispatch_to_provider_http()

    assert consumer.client.calls == 1
    assert consumer.session.previous_response_id == ""
    assert consumer._response_chain_previous_id == ""
    assert consumer._tool_output_payload is None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_dispatch_to_provider_http_fails_over_through_backups(monkeypatch):
    _seed_consumer_backup_models()
    user = get_user_model().objects.create_user(username="http-failover-user")
    workspace = Workspace.objects.create(name="http-failover-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name="HTTP Failover Agent",
        soul="Reply carefully.",
        default_model="primary-model",
        backup_models_json=[
            {"company": "google", "api": "gemini", "name": "gemini-2.5-flash"},
            {"company": "google", "api": "gemini", "name": "gemini-2.5-pro"},
        ],
        backup_retry_policy_json={
            "retry_same_model_attempts": 1,
            "retryable_status_codes": list(RETRYABLE_STATUS_CODES),
        },
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.RUNNING,
        input_text="trigger failover",
    )

    call_log: list[tuple[str, str]] = []
    clients = _build_failover_clients(call_log, retry_same_model_attempts=1)
    monkeypatch.setattr("agents.consumers.get_client", lambda provider: clients[provider])
    monkeypatch.setattr("llm.services.providers.retry.asyncio.sleep", _noop_sleep)

    consumer = AgentChatConsumer(
        scope={"type": "websocket", "user": user, "url_route": {"kwargs": {"slug": agent.slug}}}
    )
    consumer.agent = agent
    consumer.run = run
    consumer.run_id = str(run.id)
    consumer.provider = "openai"
    consumer.provider_label = "OpenAI"
    consumer.model_name = "primary-model"
    consumer.transport = "http"
    consumer.use_ws = False
    consumer.client = clients["openai"]
    consumer.session = SimpleNamespace(previous_response_id="")
    consumer.history = [
        {"role": "system", "content": "Bootstrap status: pending"},
        {"role": "user", "content": "hi"},
    ]
    consumer.tool_definitions = []
    consumer._model_candidates = [
        {"provider": "openai", "model": "primary-model", "source": "primary"},
        {"provider": "gemini", "model": "gemini-2.5-flash", "source": "backup"},
        {"provider": "gemini", "model": "gemini-2.5-pro", "source": "backup"},
    ]
    consumer._active_model_candidate_index = 0
    consumer._backup_retry_policy = {
        "retry_same_model_attempts": 1,
        "retryable_status_codes": list(RETRYABLE_STATUS_CODES),
    }
    consumer._tool_result_event = asyncio.Event()
    consumer._tool_result_event.set()
    consumer._tool_output_payload = None
    consumer._response_chain_previous_id = ""
    consumer._dispatch_blocked_by_run_status = AsyncMock(return_value=False)
    consumer._log_transport_traffic = AsyncMock()
    consumer.send_json = AsyncMock()
    monkeypatch.setattr("agents.consumers._set_run_previous_response_id", AsyncMock())
    monkeypatch.setattr("agents.consumers._persist_chat_history_event", AsyncMock())
    monkeypatch.setattr("agents.consumers.get_agent_telegram_mirror_enabled", lambda agent: False)

    await consumer._dispatch_to_provider_http()

    assert call_log == [
        ("openai", "primary-model"),
        ("openai", "primary-model"),
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-2.5-pro"),
    ]
    assert consumer.provider == "gemini"
    assert consumer.model_name == "gemini-2.5-pro"
    assert consumer.history[-1]["content"] == "final answer"
    consumer.send_json.assert_awaited_once()
    assert consumer.send_json.await_args.kwargs == {}
    assert consumer.send_json.await_args.args[0]["type"] == "message"
    assert consumer.send_json.await_args.args[0]["text"] == "final answer"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_chat_consumer_restores_last_model_for_reused_run(monkeypatch):
    workspace = Workspace.objects.create(name="chat-restore-ws")
    owner = get_user_model().objects.create_user(username="chat-restore-user")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="Restore Agent",
        soul="Preserve the active provider across reconnects.",
        default_model="gpt-5.2",
        backup_models_json=[
            {"company": "google", "api": "gemini", "name": "gemini-2.5-pro"},
        ],
    )
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        input_text="hello",
        previous_response_id="resp-gemini",
    )
    RunEvent.objects.create(
        run=run,
        seq=1,
        event_type="assistant_message",
        payload={
            "content": "Final answer from Gemini",
            "model": "gemini-2.5-pro",
            "provider_response_id": "resp-gemini",
        },
    )
    from llm.models import ModelsAvailable

    ModelsAvailable.objects.get_or_create(
        company="google",
        api="gemini",
        name="gemini-2.5-pro",
        defaults={"metadata": {}},
    )

    clients = {
        "openai": ResumeStubClient(provider="openai", transport="ws", session_previous_response_id="resp-openai"),
        "gemini": ResumeStubClient(provider="gemini", transport="http"),
    }
    monkeypatch.setattr("agents.consumers.get_client", lambda provider: clients[provider])
    monkeypatch.setattr("agents.consumers._get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr("agents.consumers._get_active_tool_approval_grants", AsyncMock(return_value=[]))

    consumer = AgentChatConsumer(
        scope={
            "type": "websocket",
            "user": owner,
            "url_route": {"kwargs": {"slug": agent.slug}},
            "query_string": f"run={run.id}".encode(),
        }
    )
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer._flush_pending_tool_results = AsyncMock()

    await consumer.connect()

    assert consumer.provider == "gemini"
    assert consumer.model_name == "gemini-2.5-pro"
    assert consumer.transport == "http"
    assert consumer.use_ws is False
    assert clients["gemini"].cleanup_calls == 0
    assert clients["openai"].cleanup_calls == 0
    assert clients["gemini"].get_session_calls == []
    assert clients["openai"].get_session_calls == []
    connected_payload = consumer.send_json.await_args_list[0].args[0]
    assert connected_payload["provider"] == "gemini"
    assert connected_payload["model"] == "gemini-2.5-pro"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_chat_consumer_advertises_subrun_capability_when_granted(monkeypatch):
    workspace = Workspace.objects.create(name="chat-subrun-workspace")
    owner = get_user_model().objects.create_user(username="chat-subrun-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="Subrun Agent",
        soul="Delegate focused work when tools allow.",
    )
    group = ToolGroup.objects.create(name="subrun-group")
    tool = Tool.objects.create(
        name="spawn_subrun",
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
    assert "Capability: Subruns" in connected_event["system_context"]
    assert any(tool_payload["name"] == "spawn_subrun" for tool_payload in connected_event["tools"])
    assert any(tool_payload["name"] == "spawn_subrun" for tool_payload in dummy_client.sent_tools)

    await communicator.disconnect()

