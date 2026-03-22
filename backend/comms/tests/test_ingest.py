import pytest

from django.contrib.auth import get_user_model

from agents.models import Agent
from core.models import Workspace
from comms.models import (
    CommsConversation,
    CommsMessage,
    PendingPairing,
    Transport,
    TransportEndpoint,
)
from comms.services.ingest import ingest_normalized_event
from comms.transports.base import NormalizedEvent
from control.models import ApprovalRequest, ControlConversation, ControlMessage
from comms.services.agent_chat_bridge import paired_agent_for_conversation


def make_event(**overrides):
    base = {
        "kind": "message",
        "update_id": 123,
        "chat_id": "chat-1",
        "from_user_id": "42",
        "from_username": "alice",
        "text": "hello",
        "message_id": "100",
        "callback_data": None,
        "callback_query_id": None,
        "ts": 1680000000,
    }
    base.update(overrides)
    return NormalizedEvent(**base)


@pytest.mark.django_db
def test_ingest_creates_comms_and_control_message():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"]},
    )
    event = make_event(update_id=456, message_id="200")
    convo_uuid, control_message_id = ingest_normalized_event(
        transport.key, endpoint.id, event
    )
    assert convo_uuid
    assert control_message_id
    assert ControlConversation.objects.filter(uuid=convo_uuid).exists()
    assert ControlMessage.objects.filter(id=control_message_id).exists()
    comms_message = transport.conversations.first().messages.first()
    assert comms_message.text == "hello"
    assert transport.conversations.first().endpoint == endpoint


@pytest.mark.django_db
def test_ingest_is_idempotent():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"]},
    )
    event = make_event(update_id=890, message_id="201")
    first = ingest_normalized_event(transport.key, endpoint.id, event)
    second = ingest_normalized_event(transport.key, endpoint.id, event)
    assert first == second
    assert CommsConversation.objects.filter(transport=transport).count() == 1


@pytest.mark.django_db
def test_ingest_skip_disallowed_user():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport, kind="bot", config={"allow_user_ids": ["42"]}
    )
    event = make_event(from_user_id="99", message_id="202")
    convo_uuid, control_message_id = ingest_normalized_event(
        transport.key, endpoint.id, event
    )
    assert convo_uuid
    control_message = ControlMessage.objects.get(id=control_message_id)
    assert control_message.direction == "system"
    assert "Blocked" in control_message.text


@pytest.mark.django_db
def test_ingest_callback_triggers_handler():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"]},
    )
    request = ApprovalRequest.objects.create(
        tool_name="shell_exec",
        summary="test",
        payload_preview={},
        constraints={},
    )
    event = make_event(
        kind="callback",
        update_id=789,
        message_id="203",
        callback_data=f"approve_once:{request.uuid}",
        callback_query_id="cq-1",
    )
    ingest_normalized_event(transport.key, endpoint.id, event)
    request.refresh_from_db()
    assert request.status == ApprovalRequest.STATUS_APPROVED
    assert ControlMessage.objects.filter(direction="system").exists()


@pytest.mark.django_db
def test_pairing_command_claims_pending_pairing(monkeypatch):
    workspace = Workspace.objects.create(name="pairing-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        name="broker",
        description="test",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"], "agent_id": str(agent.id)},
    )
    pairing = PendingPairing.objects.create(agent=agent, endpoint=endpoint)

    sent = []

    def fake_send(_endpoint, _chat_id, text, **kwargs):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr("comms.services.ingest.send_telegram_message", fake_send)

    event = make_event(text=f"/pair {pairing.pair_code}", from_user_id="42")
    convo_uuid, control_message_id = ingest_normalized_event(
        transport.key, endpoint.id, event
    )

    control_message = ControlMessage.objects.get(id=control_message_id)
    pairing.refresh_from_db()

    assert pairing.status == PendingPairing.STATUS_CLAIMED
    assert pairing.claimed_chat_id == event.chat_id
    assert agent.default_conversation
    assert control_message.direction == "system"
    assert "Paired" in control_message.text
    assert sent


@pytest.mark.django_db
def test_pairing_invalid_code_reports_failure(monkeypatch):
    workspace = Workspace.objects.create(name="pairing-ws-2")
    agent = Agent.objects.create(
        workspace=workspace,
        name="broker-two",
        description="test invalid",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"], "agent_id": str(agent.id)},
    )
    PendingPairing.objects.create(agent=agent, endpoint=endpoint)

    sent = []

    def fake_send(_endpoint, _chat_id, text, **kwargs):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr("comms.services.ingest.send_telegram_message", fake_send)

    event = make_event(text="/pair WRONGCODE", from_user_id="42")
    convo_uuid, control_message_id = ingest_normalized_event(
        transport.key, endpoint.id, event
    )

    control_message = ControlMessage.objects.get(id=control_message_id)
    assert control_message.direction == "system"
    assert "Invalid or expired pairing code" in control_message.text
    assert sent


@pytest.mark.django_db
def test_paired_agent_for_conversation_falls_back_to_default_conversation():
    workspace = Workspace.objects.create(name="pairing-fallback-ws")
    owner = get_user_model().objects.create_user(username="pairing-fallback-owner")
    agent = Agent.objects.create(
        workspace=workspace,
        owner=owner,
        name="fallback-agent",
        description="test fallback",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are an agent.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"], "agent_id": str(agent.id)},
    )
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Fallback")
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])
    conversation = CommsConversation.objects.create(
        transport=transport,
        endpoint=endpoint,
        external_conversation_id="chat-fallback",
        control_conversation=control_conversation,
    )

    resolved = paired_agent_for_conversation(conversation)

    assert resolved == agent
