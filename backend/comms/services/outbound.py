import asyncio
import logging
from typing import Mapping, Optional

from django.utils import timezone

from comms.models import CommsConversation, CommsMessage, TransportEndpoint
from control.models import ControlConversation, ControlMessage
from control.services.messaging import broadcast_control_message
from comms.transports.telegram import TelegramAdapter

logger = logging.getLogger(__name__)


def _find_bot_endpoint(conversation: CommsConversation) -> TransportEndpoint:
    if conversation.endpoint:
        return conversation.endpoint

    endpoint = (
        TransportEndpoint.objects.filter(
            transport=conversation.transport, kind="bot", transport__is_enabled=True
        )
        .order_by("id")
        .first()
    )
    if not endpoint:
        raise RuntimeError(f"No bot endpoint configured for {conversation.transport.key}")
    return endpoint


def send_telegram_message(
    endpoint: TransportEndpoint, chat_id: str, text: str, **kwargs: object
) -> Mapping[str, object]:
    async def _inner() -> Mapping[str, object]:
        async with TelegramAdapter() as adapter:
            return await adapter.send_message(endpoint, chat_id, text, **kwargs)

    return asyncio.run(_inner())


def send_telegram_text(
    conversation: CommsConversation, text: str, actor_label: Optional[str] = None
) -> Mapping[str, object]:
    if not conversation.external_conversation_id:
        raise ValueError("Conversation is missing an external chat ID")

    endpoint = _find_bot_endpoint(conversation)
    response = send_telegram_message(
        endpoint, conversation.external_conversation_id, text
    )
    result = (response or {}).get("result") or {}
    message_id = str(result.get("message_id") or "")
    payload = {
        "sent_at": timezone.now().isoformat(),
        "response": result,
    }

    comms_message = CommsMessage.objects.create(
        conversation=conversation,
        external_message_id=message_id,
        direction="out",
        text=text,
        payload=payload,
        created_at=timezone.now(),
    )

    conversation.updated_at = timezone.now()
    conversation.save(update_fields=["updated_at"])

    control_conversation = conversation.control_conversation
    if not control_conversation:
        control_conversation = ControlConversation.objects.create(
            kind="comms_mirror",
            title=conversation.title or conversation.transport.display_name,
        )
        conversation.control_conversation = control_conversation
        conversation.save(update_fields=["control_conversation"])

    control_message = ControlMessage.objects.create(
        conversation=control_conversation,
        direction="out",
        author_type="operator",
        author_label=actor_label or "operator",
        text=text,
        payload={"sent": payload},
        source_transport=conversation.transport.key,
        source_conversation_id=conversation.external_conversation_id,
        source_message_id=message_id,
    )
    broadcast_control_message(control_message)

    return {
        "response": response,
        "comms_message_id": comms_message.id,
        "control_message_id": control_message.id,
    }


def get_telegram_bot_info(endpoint: TransportEndpoint) -> Mapping[str, object]:
    async def _inner() -> Mapping[str, object]:
        async with TelegramAdapter() as adapter:
            return await adapter.get_me(endpoint)

    return asyncio.run(_inner())
