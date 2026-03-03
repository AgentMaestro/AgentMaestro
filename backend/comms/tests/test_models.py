import pytest

from comms.models import (Transport, TransportEndpoint, ExternalIdentity, CommsConversation, CommsMessage)
from control.models import ControlConversation


@pytest.mark.django_db
def test_comms_models_linked_to_control():
    transport = Transport.objects.create(key="telegram", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={})
    control_convo = ControlConversation.objects.create(kind="comms_mirror", title="tg chat")
    comms_convo = CommsConversation.objects.create(
        transport=transport,
        external_conversation_id="chat-1",
        control_conversation=control_convo,
        endpoint=endpoint,
    )
    identity = ExternalIdentity.objects.create(
        transport=transport,
        external_user_id="user-42",
        username="user42",
    )
    message = CommsMessage.objects.create(
        conversation=comms_convo,
        direction="in",
        sender=identity,
        text="hello",
    )
    assert comms_convo.control_conversation == control_convo
    assert comms_convo.messages.first() == message
    assert message.sender == identity
    assert transport.endpoints.first() == endpoint
