import pytest
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from comms.models import CommsConversation, Transport, TransportEndpoint
from comms.services.agent_chat_bridge import active_run_for_agent, paired_conversation_for_agent
from comms.services.outbound import send_telegram_message, send_telegram_text
from control.models import ControlConversation, ControlMessage
from agents.models import Agent
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
