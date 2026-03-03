import pytest

from comms.models import CommsConversation, Transport, TransportEndpoint
from comms.services.outbound import send_telegram_message, send_telegram_text
from control.models import ControlConversation, ControlMessage


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
