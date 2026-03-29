from datetime import datetime, timedelta

import pytest

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.test import override_settings

from agents.models import Agent
from comms.models import CommsConversation, Transport, TransportEndpoint
from comms.services.agent_chat_bridge import (
    active_run_for_agent,
    paired_conversation_for_agent,
    send_paired_telegram_message,
    send_run_transport_message,
)
from comms.services.outbound import send_telegram_message, send_telegram_text
from comms.services.telegram_markup import render_mirror_telegram_html
from control.models import ControlConversation, ControlMessage
from core.models import Workspace
from runs.models import AgentRun


@pytest.mark.django_db
def test_send_telegram_text_creates_mirrored_messages(monkeypatch):
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Ops")
    conversation = CommsConversation.objects.create(
        transport=transport,
        external_conversation_id="chat-1",
        control_conversation=control_conversation,
        endpoint=endpoint,
    )

    def fake_send(endpoint_arg, chat_id, text, **kwargs):
        assert chat_id == "chat-1"
        return {"ok": True, "result": {"message_id": 555}}

    monkeypatch.setattr("comms.services.outbound.send_telegram_message", fake_send)
    monkeypatch.setattr("comms.services.outbound.broadcast_control_message", lambda message: None)

    payload = send_telegram_text(conversation, "hello", actor_label="tester")

    assert payload["control_message_id"]
    assert ControlMessage.objects.filter(direction="out").exists()
    assert payload["response"]["ok"] is True


@pytest.mark.django_db
def test_send_conversation_message_requires_paired_endpoint():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Ops")
    conversation = CommsConversation.objects.create(
        transport=transport,
        external_conversation_id="chat-missing-endpoint",
        control_conversation=control_conversation,
        endpoint=None,
    )

    with pytest.raises(RuntimeError, match="missing its paired/main endpoint"):
        send_telegram_text(conversation, "hello", actor_label="tester")


@pytest.mark.django_db
def test_paired_conversation_for_agent_falls_back_to_default_conversation():
    workspace = Workspace.objects.create(name="pairing-fallback-outbound-ws")
    owner = get_user_model().objects.create_user(username="pairing-fallback-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="outbound-fallback-agent",
        description="test fallback",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Outbound Fallback")
    conversation = CommsConversation.objects.create(
        transport=transport,
        endpoint=endpoint,
        external_conversation_id="chat-outbound-fallback",
        control_conversation=control_conversation,
    )
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])

    resolved = paired_conversation_for_agent(agent)

    assert resolved == conversation


@pytest.mark.django_db
def test_active_run_for_agent_prefers_dashboard_runs():
    workspace = Workspace.objects.create(name="active-run-ws")
    owner = get_user_model().objects.create_user(username="active-run-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="active-run-agent",
        description="test active run preference",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )

    dashboard_run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.USER_CHAT,
        input_text="dashboard run",
    )
    telegram_run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.TELEGRAM,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.USER_CHAT,
        input_text="telegram run",
    )

    dashboard_run.started_at = timezone.now()
    dashboard_run.save(update_fields=["started_at"])
    telegram_run.started_at = dashboard_run.started_at + timedelta(seconds=10)
    telegram_run.save(update_fields=["started_at"])

    resolved = active_run_for_agent(agent)

    assert resolved == dashboard_run


@pytest.mark.django_db
def test_send_run_transport_message_uses_markdownv2_escaped_plain_text(monkeypatch):
    workspace = Workspace.objects.create(name="telegram-markdownv2-ws")
    owner = get_user_model().objects.create_user(username="telegram-markdownv2-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="markdown-agent",
        description="test markdown rendering",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Markdown")
    conversation = CommsConversation.objects.create(
        transport=transport,
        endpoint=endpoint,
        external_conversation_id="chat-markdownv2",
        control_conversation=control_conversation,
    )
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.USER_CHAT,
        input_text="markdown run",
    )
    run.started_at = timezone.now()
    run.save(update_fields=["started_at"])

    captured: dict[str, object] = {}

    def fake_send(conversation_arg, text, **kwargs):
        captured["conversation"] = conversation_arg
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {"response": {"ok": True}, "comms_message_id": 1, "control_message_id": 2}

    monkeypatch.setattr("comms.services.agent_chat_bridge.send_conversation_message", fake_send)

    delivered = send_run_transport_message(
        run_id=str(run.id),
        text="Hello *world* (test) _markdown_",
        author_label="Assistant",
    )

    assert delivered is True
    assert captured["conversation"] == conversation
    assert captured["kwargs"]["parse_mode"] == "HTML"
    assert captured["text"] == "<b>markdown-agent</b>\nHello <i>world</i> (test) <i>markdown</i>"


@pytest.mark.django_db
def test_send_run_transport_message_honors_explicit_parse_mode(monkeypatch):
    workspace = Workspace.objects.create(name="telegram-explicit-parse-mode-ws")
    owner = get_user_model().objects.create_user(username="telegram-explicit-parse-mode-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="markup-agent",
        description="test explicit parse mode",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Markup")
    CommsConversation.objects.create(
        transport=transport,
        endpoint=endpoint,
        external_conversation_id="chat-markup",
        control_conversation=control_conversation,
    )
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=owner,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.USER_CHAT,
        input_text="markup run",
    )
    run.started_at = timezone.now()
    run.save(update_fields=["started_at"])

    captured: dict[str, object] = {}

    def fake_send(conversation_arg, text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {"response": {"ok": True}, "comms_message_id": 1, "control_message_id": 2}

    monkeypatch.setattr("comms.services.agent_chat_bridge.send_conversation_message", fake_send)

    delivered = send_run_transport_message(
        run_id=str(run.id),
        text="Hello *world*",
        author_label="Assistant",
        parse_mode="HTML",
    )

    assert delivered is True
    assert captured["kwargs"]["parse_mode"] == "HTML"
    assert captured["text"] == "Hello *world*"


@pytest.mark.django_db
@override_settings(TIME_ZONE="America/New_York")
def test_send_paired_telegram_message_prepends_name_and_timestamp(monkeypatch):
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Ops")
    owner = get_user_model().objects.create_user(username="telegram-owner", password="x")
    workspace = Workspace.objects.create(name="Workspace Telegram")
    agent = Agent.objects.create(name="Agent One", slug="agent-one", owner=owner, workspace=workspace)
    conversation = CommsConversation.objects.create(
        transport=transport,
        external_conversation_id="chat-1",
        control_conversation=control_conversation,
        endpoint=endpoint,
    )
    agent.default_conversation = conversation.control_conversation
    agent.save(update_fields=["default_conversation"])
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=get_user_model().objects.create_user(username="tg-user", password="x"),
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        execution_mode=AgentRun.ExecutionMode.INTERACTIVE,
        trigger_kind=AgentRun.TriggerKind.USER_CHAT,
        input_text="telegram tool",
    )
    run.started_at = timezone.now()
    run.save(update_fields=["started_at"])

    captured: dict[str, object] = {}

    def fake_send(conversation_arg, text, **kwargs):
        captured["text"] = text
        captured["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": 777}}, "comms_message_id": 1, "control_message_id": 2}

    fixed_local = timezone.make_aware(datetime(2026, 3, 29, 10, 24), timezone.get_current_timezone())
    monkeypatch.setattr("comms.services.agent_chat_bridge.send_conversation_message", fake_send)
    monkeypatch.setattr("comms.services.agent_chat_bridge.timezone.localtime", lambda dt: fixed_local)

    payload = send_paired_telegram_message(
        run_id=str(run.id),
        text="Hello from the agent.",
        name="System - Task Complete",
    )

    assert payload["delivered"] is True
    assert captured["kwargs"]["parse_mode"] == "HTML"
    assert captured["text"] == "<b>system - task complete   10:24am</b>\nHello from the agent."


def test_render_mirror_telegram_html_flattens_headings_lists_blockquotes_tables_and_code_blocks():
    rendered = render_mirror_telegram_html(
        author_label="jeeves",
        body=(
            "# Heading 1\n"
            "## Heading 2\n"
            "> This is a quote.\n"
            "> It continues on the next line.\n"
            "\n"
            "| Name | Role | Status |\n"
            "|------|------|--------|\n"
            "| Alice | Admin | Active |\n"
            "| Bob | User | Pending |\n"
            "\n"
            "- Item one\n"
            "  - Nested item\n"
            "1. First step\n"
            "```python\n"
            "def hello(name):\n"
            "    print(f\"Hello, {name}!\")\n"
            "```\n"
        ),
    )

    assert rendered.startswith("<b>jeeves</b>\n")
    assert "<b>Heading 1</b>" in rendered
    assert "<b>Heading 2</b>" in rendered
    assert "<blockquote>This is a quote.<br>It continues on the next line.</blockquote>" in rendered
    assert "<b>Name</b> | <b>Role</b> | <b>Status</b>" in rendered
    assert "<b>Name</b>: Alice | <b>Role</b>: Admin | <b>Status</b>: Active" in rendered
    assert "<b>Name</b>: Bob | <b>Role</b>: User | <b>Status</b>: Pending" in rendered
    assert "&#8226; Item one" in rendered
    assert "&#8226; Nested item" in rendered
    assert "&#8226; First step" in rendered
    assert "<pre><code>def hello(name):\n    print(f&quot;Hello, {name}!&quot;)</code></pre>" in rendered
