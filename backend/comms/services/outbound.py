import asyncio
from typing import Mapping, Optional

from django.utils import timezone

from comms.models import CommsConversation, CommsMessage, TransportEndpoint
from control.models import ControlConversation, ControlMessage
from control.services.messaging import broadcast_control_message
from comms.transports.telegram import TelegramAdapter
from logging_utils import get_app_logger, scrub_sensitive_text, scrub_sensitive_value

logger = get_app_logger(__name__)


def _find_bot_endpoint(conversation: CommsConversation) -> TransportEndpoint:
    if conversation.endpoint:
        return conversation.endpoint
    raise RuntimeError(
        f"Telegram conversation {conversation.external_conversation_id} is missing its paired/main endpoint"
    )


def send_transport_message(
    endpoint: TransportEndpoint, chat_id: str, text: str, **kwargs: object
) -> Mapping[str, object]:
    if endpoint.transport.key != "telegram":
        raise RuntimeError(
            f"Transport {endpoint.transport.key} does not support outbound messaging yet"
        )
    safe_text = scrub_sensitive_text(text)

    async def _inner() -> Mapping[str, object]:
        async with TelegramAdapter() as adapter:
            return await adapter.send_message(endpoint, chat_id, safe_text, **kwargs)

    return asyncio.run(_inner())


def edit_transport_message(
    endpoint: TransportEndpoint, chat_id: str, message_id: str, text: str, **kwargs: object
) -> Mapping[str, object]:
    if endpoint.transport.key != "telegram":
        raise RuntimeError(
            f"Transport {endpoint.transport.key} does not support outbound edits yet"
        )
    safe_text = scrub_sensitive_text(text)

    async def _inner() -> Mapping[str, object]:
        async with TelegramAdapter() as adapter:
            return await adapter.edit_message(endpoint, chat_id, message_id, safe_text, **kwargs)

    return asyncio.run(_inner())


def send_conversation_message(
    conversation: CommsConversation,
    text: str,
    *,
    actor_label: Optional[str] = None,
    author_type: str = "operator",
    control_direction: str = "out",
    control_payload: Optional[dict[str, object]] = None,
    mirror_to_control: bool = True,
    **kwargs: object,
) -> Mapping[str, object]:
    if not conversation.external_conversation_id:
        raise ValueError("Conversation is missing an external chat ID")

    endpoint = _find_bot_endpoint(conversation)
    safe_text = scrub_sensitive_text(text)
    response = send_transport_message(
        endpoint, conversation.external_conversation_id, safe_text, **kwargs
    )
    result = (response or {}).get("result") or {}
    message_id = str(result.get("message_id") or "")
    payload = {
        "sent_at": timezone.now().isoformat(),
        "response": scrub_sensitive_value(result),
    }
    if kwargs:
        payload["request"] = scrub_sensitive_value(kwargs)

    comms_message = CommsMessage.objects.create(
        conversation=conversation,
        external_message_id=message_id,
        direction="out",
        text=safe_text,
        payload=payload,
        created_at=timezone.now(),
    )

    conversation.updated_at = timezone.now()
    conversation.save(update_fields=["updated_at"])

    control_message_id = None
    if mirror_to_control:
        control_conversation = conversation.control_conversation
        if not control_conversation:
            control_conversation = ControlConversation.objects.create(
                kind="comms_mirror",
                title=conversation.title or conversation.transport.display_name,
            )
            conversation.control_conversation = control_conversation
            conversation.save(update_fields=["control_conversation"])

        full_control_payload = dict(control_payload or {})
        full_control_payload["sent"] = payload
        control_message = ControlMessage.objects.create(
            conversation=control_conversation,
            direction=control_direction,
            author_type=author_type,
            author_label=actor_label or author_type,
            text=safe_text,
            payload=scrub_sensitive_value(full_control_payload),
            source_transport=conversation.transport.key,
            source_conversation_id=conversation.external_conversation_id,
            source_message_id=message_id,
        )
        broadcast_control_message(control_message)
        control_message_id = control_message.id

    return {
        "response": response,
        "comms_message_id": comms_message.id,
        "control_message_id": control_message_id,
    }


def edit_conversation_message(
    conversation: CommsConversation,
    message_id: str,
    text: str,
    **kwargs: object,
) -> Mapping[str, object]:
    if not conversation.external_conversation_id:
        raise ValueError("Conversation is missing an external chat ID")
    endpoint = _find_bot_endpoint(conversation)
    response = edit_transport_message(
        endpoint,
        conversation.external_conversation_id,
        message_id,
        text,
        **kwargs,
    )
    result = (response or {}).get("result") or {}
    CommsMessage.objects.filter(
        conversation=conversation,
        external_message_id=str(message_id or ""),
    ).update(
        text=scrub_sensitive_text(text),
        payload={
            "edited_at": timezone.now().isoformat(),
            "response": scrub_sensitive_value(result),
            "request": scrub_sensitive_value(kwargs or {}),
        },
        created_at=timezone.now(),
    )
    conversation.updated_at = timezone.now()
    conversation.save(update_fields=["updated_at"])
    return {"response": response}


def send_telegram_message(
    endpoint: TransportEndpoint, chat_id: str, text: str, **kwargs: object
) -> Mapping[str, object]:
    return send_transport_message(endpoint, chat_id, text, **kwargs)


def send_telegram_text(
    conversation: CommsConversation, text: str, actor_label: Optional[str] = None
) -> Mapping[str, object]:
    return send_conversation_message(conversation, text, actor_label=actor_label or "operator")


def get_telegram_bot_info(endpoint: TransportEndpoint) -> Mapping[str, object]:
    async def _inner() -> Mapping[str, object]:
        async with TelegramAdapter() as adapter:
            return await adapter.get_me(endpoint)

    return asyncio.run(_inner())
