from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

import pytest

from agents.models import Agent
from comms.models import CommsConversation, PendingPairing, Transport, TransportEndpoint
from control.models import ControlConversation, ControlMessage
from core.models import Workspace

User = get_user_model()


@pytest.mark.django_db
def test_chat_home_requires_login():
    client = Client()
    response = client.get(reverse("control:chat_home"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_chat_home_renders_conversation():
    user = User.objects.create_user(username="op", password="secret")
    conversation = ControlConversation.objects.create(kind="comms_mirror", title="Ops")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("control:chat_home"))
    assert response.status_code == 200
    assert "Control Chat" in response.content.decode()
    assert conversation.title in response.content.decode()


@pytest.mark.django_db
def test_chat_send_posts_and_calls_telegram(monkeypatch):
    user = User.objects.create_user(username="op", password="secret")
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    conversation = ControlConversation.objects.create(kind="comms_mirror", title="Ops")
    comms = CommsConversation.objects.create(
        transport=transport,
        external_conversation_id="chat-1",
        control_conversation=conversation,
        endpoint=endpoint,
    )
    called = []

    def fake_send(comms_obj, text, actor_label=None):
        called.append((comms_obj, text, actor_label))
        return {"ok": True}

    monkeypatch.setattr("control.views.send_telegram_text", fake_send)
    monkeypatch.setattr("control.views.broadcast_control_message", lambda message: None)

    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("control:chat_send", kwargs={"uuid": conversation.uuid}),
        {"message": "hello"},
    )
    assert response.status_code == 302
    assert called
    assert called[0][1] == "hello"
    assert called[0][2] == user.username
    assert ControlMessage.objects.filter(direction="out").count() == 1


@pytest.mark.django_db
def test_connect_telegram_get():
    user = User.objects.create_user(username="op", password="secret")
    workspace = Workspace.objects.create(name="connect-ws")
    Agent.objects.create(
        workspace=workspace,
        name="agent-one",
        description="",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are agent-one.",
        policy_name="react",
        tool_policy_json={},
    )
    agent = Agent.objects.first()
    client = Client()
    client.force_login(user)
    response = client.get(
        reverse("ui:connect_telegram", kwargs={"agent_uuid": agent.id})
    )
    assert response.status_code == 200
    assert "Connect Telegram" in response.content.decode()


@pytest.mark.django_db
def test_connect_telegram_post_creates_endpoint_and_pairing(monkeypatch):
    user = User.objects.create_user(username="op", password="secret")
    workspace = Workspace.objects.create(name="connect-ws-2")
    Agent.objects.create(
        workspace=workspace,
        name="agent-two",
        description="",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are agent-two.",
        policy_name="react",
        tool_policy_json={},
    )
    agent = Agent.objects.first()
    client = Client()
    client.force_login(user)

    def fake_bot_info(endpoint):
        return {"id": 618, "username": "agent_maestro_bot", "first_name": "Agent", "last_name": "Maestro"}

    monkeypatch.setattr("control.views.get_telegram_bot_info", fake_bot_info)

    response = client.post(
        reverse("ui:connect_telegram", kwargs={"agent_uuid": agent.id}),
        {"allow_user_ids": "42"},
    )
    assert response.status_code == 200
    assert TransportEndpoint.objects.filter(transport__key="telegram").exists()
    assert PendingPairing.objects.filter(agent=agent).exists()


@pytest.mark.django_db
def test_pairing_status_view_returns_json():
    user = User.objects.create_user(username="op", password="secret")
    workspace = Workspace.objects.create(name="pairing-status-ws")
    agent = Agent.objects.create(
        workspace=workspace,
        name="agent-status",
        description="",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are agent-status.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    pairing = PendingPairing.objects.create(agent=agent, endpoint=endpoint)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid}))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == PendingPairing.STATUS_PENDING
    assert payload["pair_code"] == pairing.pair_code


@pytest.mark.django_db
def test_pairing_status_claimed_redirects():
    user = User.objects.create_user(username="op", password="secret")
    workspace = Workspace.objects.create(name="pairing-status-ws-2")
    agent = Agent.objects.create(
        workspace=workspace,
        name="agent-status-two",
        description="",
        default_model="gpt-5",
        temperature=0.70,
        soul="You are agent-status-two.",
        policy_name="react",
        tool_policy_json={},
    )
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    pairing = PendingPairing.objects.create(agent=agent, endpoint=endpoint)
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title="Status")
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])
    pairing.status = PendingPairing.STATUS_CLAIMED
    pairing.save(update_fields=["status"])

    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid}))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == PendingPairing.STATUS_CLAIMED
    assert "redirect_url" in payload
