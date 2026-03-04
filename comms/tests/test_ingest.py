import pytest

from comms.models import CommsMessage, Transport, TransportEndpoint
from comms.services.ingest import ingest_normalized_event
from comms.transports.base import NormalizedEvent
from control.models import ControlConversation, ControlMessage


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
    event = make_event()
    convo_uuid, control_message_id = ingest_normalized_event(
        transport.key, endpoint.id, event
    )
    assert convo_uuid
    assert control_message_id
    assert ControlConversation.objects.filter(uuid=convo_uuid).exists()
    assert ControlMessage.objects.filter(id=control_message_id).exists()
    comms_message = transport.conversations.first().messages.first()
    assert comms_message.text == "hello"


@pytest.mark.django_db
def test_ingest_is_idempotent():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(
        transport=transport,
        kind="bot",
        config={"allow_user_ids": ["42"]},
    )
    event = make_event()
    first = ingest_normalized_event(transport.key, endpoint.id, event)
    second = ingest_normalized_event(transport.key, endpoint.id, event)
    assert first == second
    assert CommsConversation.objects.filter(transport=transport).count() == 1


@pytest.mark.django_db
def test_ingest_skip_disallowed_user():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    event = make_event(from_user_id="99")
    result = ingest_normalized_event(transport.key, endpoint.id, event)
    assert result == (None, None)
    assert ControlMessage.objects.count() == 0
