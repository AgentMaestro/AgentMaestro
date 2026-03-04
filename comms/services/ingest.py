from __future__ import annotations

from typing import Optional

from django.db import IntegrityError, transaction

from comms.models import (
    CommsConversation,
    CommsMessage,
    ExternalIdentity,
    TransportEndpoint,
)
from comms.transports.base import NormalizedEvent
from control.models import ControlConversation, ControlMessage, IngestEvent


def ingest_normalized_event(
    transport_key: str, endpoint_id: int, event: NormalizedEvent
) -> tuple[Optional[str], Optional[int]]:
    try:
        IngestEvent.objects.create(
            transport=transport_key, external_event_id=str(event.update_id)
        )
    except IntegrityError:
        existing_message = (
            CommsMessage.objects.filter(
                external_message_id=event.message_id or "",
                conversation__transport__key=transport_key,
            )
            .order_by("-created_at")
            .first()
        )
        if existing_message and existing_message.conversation.control_conversation:
            control_convo = existing_message.conversation.control_conversation
            return (str(control_convo.uuid), control_convo.messages.filter(id=existing_message.id).first().id)
        return (None, None)

    endpoint = TransportEndpoint.objects.select_related("transport").get(pk=endpoint_id)
    allow_user_ids = endpoint.config.get("allow_user_ids", []) or []
    allow_user_ids = [str(u) for u in allow_user_ids]
    if allow_user_ids and event.from_user_id not in allow_user_ids:
        return (None, None)

    with transaction.atomic():
        identity, _ = ExternalIdentity.objects.get_or_create(
            transport=endpoint.transport,
            external_user_id=event.from_user_id,
            defaults={
                "username": event.from_username or "",
                "display_name": event.from_username or "",
            },
        )
        conversation = (
            CommsConversation.objects.select_for_update()
            .filter(
                transport=endpoint.transport, external_conversation_id=event.chat_id
            )
            .first()
        )
        if not conversation:
            control_convo = ControlConversation.objects.create(
                kind="comms_mirror", title=f"{endpoint.transport.display_name}:{event.chat_id}"
            )
            conversation = CommsConversation.objects.create(
                transport=endpoint.transport,
                external_conversation_id=event.chat_id,
                control_conversation=control_convo,
            )
        else:
            if not conversation.control_conversation:
                conversation.control_conversation = ControlConversation.objects.create(
                    kind="comms_mirror",
                    title=f"{endpoint.transport.display_name}:{event.chat_id}",
                )
                conversation.save(update_fields=["control_conversation"])

        comms_message = CommsMessage.objects.create(
            conversation=conversation,
            external_message_id=event.message_id or "",
            direction="in",
            sender=identity,
            text=event.text or event.callback_data or "",
            payload={
                "kind": event.kind,
                "callback_query_id": event.callback_query_id,
            },
        )
        control_convo = conversation.control_conversation
        control_message = ControlMessage.objects.create(
            conversation=control_convo,
            direction="in",
            author_type="transport_user",
            author_label=identity.display_name or identity.external_user_id,
            text=comms_message.text,
            payload={"event_id": event.update_id},
            source_transport=endpoint.transport.key,
            source_conversation_id=event.chat_id,
            source_message_id=comms_message.external_message_id,
        )
        return (str(control_convo.uuid), control_message.id)
