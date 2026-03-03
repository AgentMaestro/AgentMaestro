from __future__ import annotations

import httpx
import logging
from typing import Iterable, Optional, Tuple

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from comms.models import (
    CommsConversation,
    CommsMessage,
    ExternalIdentity,
    PendingPairing,
    Transport,
    TransportEndpoint,
)
from control.models import ControlConversation, ControlMessage, IngestEvent
from control.services.messaging import broadcast_control_message
from comms.transports.base import NormalizedEvent
from comms.services.outbound import send_telegram_message

logger = logging.getLogger(__name__)


def _normalize_user_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _allowed_user_ids(endpoint: TransportEndpoint) -> Optional[set[str]]:
    allowed = endpoint.config.get("allow_user_ids")
    if allowed is None:
        return None
    return {str(item) for item in allowed if item is not None}


def _record_ingest_event(transport_key: str, update_id: int) -> Tuple[IngestEvent, bool]:
    try:
        obj, created = IngestEvent.objects.get_or_create(
            transport=transport_key, external_event_id=str(update_id)
        )
        return obj, created
    except IntegrityError:
        transaction.set_rollback(False)
        existing = IngestEvent.objects.get(
            transport=transport_key, external_event_id=str(update_id)
        )
        return existing, False


def _existing_message_result(
    transport: Transport, endpoint: TransportEndpoint, event: NormalizedEvent
) -> Tuple[Optional[str], Optional[int]]:
    if not event.message_id:
        return None, None

    conversation = CommsConversation.objects.filter(
        endpoint=endpoint, external_conversation_id=event.chat_id
    ).select_related("control_conversation").first()
    if not conversation or not conversation.control_conversation:
        return None, None

    message = CommsMessage.objects.filter(
        conversation=conversation,
        external_message_id=_normalize_user_id(event.message_id) or "",
    ).order_by("-created_at").first()
    if not message:
        return None, None
    return str(conversation.control_conversation.uuid), message.id


@transaction.atomic
def ingest_normalized_event(
    transport_key: str, endpoint_id: int, event: NormalizedEvent
) -> Tuple[Optional[str], Optional[int]]:
    transport = Transport.objects.get(key=transport_key)
    endpoint = TransportEndpoint.objects.select_for_update().get(id=endpoint_id)

    ingest_event, recorded = _record_ingest_event(transport_key, event.update_id)
    if not recorded:
        meta = ingest_event.result_meta or {}
        stored_uuid = meta.get("conversation_uuid")
        stored_message_id = meta.get("control_message_id")
        if stored_uuid or stored_message_id:
            return stored_uuid, stored_message_id
        return _existing_message_result(transport, endpoint, event)

    incoming_user_id = str(event.from_user_id)
    allowed_ids = _allowed_user_ids(endpoint)
    restricted = allowed_ids is not None and incoming_user_id not in allowed_ids

    identity_allowed = not restricted
    identity, created = ExternalIdentity.objects.get_or_create(
        transport=transport,
        external_user_id=incoming_user_id,
        defaults={
            "username": event.from_username or "",
            "display_name": event.from_username or incoming_user_id,
            "is_allowed": identity_allowed,
        },
    )

    if not created:
        updated_fields: list[str] = []
        if event.from_username:
            if event.from_username != identity.username:
                identity.username = event.from_username
                updated_fields.append("username")
            if event.from_username != identity.display_name:
                identity.display_name = event.from_username
                updated_fields.append("display_name")
        if identity.is_allowed != identity_allowed:
            identity.is_allowed = identity_allowed
            updated_fields.append("is_allowed")
        if updated_fields:
            identity.save(update_fields=updated_fields)

    conversation, _ = CommsConversation.objects.get_or_create(
        endpoint=endpoint,
        external_conversation_id=event.chat_id,
        defaults={
            "transport": transport,
            "title": endpoint.config.get("default_conversation_title", ""),
        },
    )
    conversation.updated_at = timezone.now()
    conversation.save(update_fields=["updated_at"])

    payload: dict[str, object] = {
        "kind": event.kind,
        "update_id": event.update_id,
    }
    if event.callback_data:
        payload["callback_data"] = event.callback_data
    if event.callback_query_id:
        payload["callback_query_id"] = event.callback_query_id
    message_text = event.text or ""

    comms_message = CommsMessage.objects.create(
        conversation=conversation,
        external_message_id=_normalize_user_id(event.message_id) or "",
        direction="in",
        sender=identity,
        text=message_text,
        payload=payload,
        created_at=timezone.now(),
    )

    control_conversation = conversation.control_conversation
    if not control_conversation:
        control_conversation = ControlConversation.objects.create(
            kind="comms_mirror",
            title=conversation.title or transport.display_name,
        )
        conversation.control_conversation = control_conversation
        conversation.save(update_fields=["control_conversation"])

    source_message_id = _normalize_user_id(event.message_id)

    if not restricted:
        pairing_message = _maybe_handle_pairing_command(
            endpoint,
            conversation,
            control_conversation,
            event,
            message_text,
            dict(payload),
            source_message_id,
        )
        if pairing_message:
            ingest_event.result_meta = {
                "conversation_uuid": str(control_conversation.uuid),
                "control_message_id": pairing_message.id,
            }
            ingest_event.save(update_fields=["result_meta"])
            return str(control_conversation.uuid), pairing_message.id

    if restricted:
        unauthorized_payload = dict(payload)
        unauthorized_payload["unauthorized_user_id"] = incoming_user_id
        unauthorized_payload["original_text"] = message_text
        control_message = _create_system_control_message(
            control_conversation,
            "Blocked message from unauthorized user",
            unauthorized_payload,
            transport.key,
            conversation.external_conversation_id,
            source_message_id,
        )
        ingest_event.result_meta = {
            "conversation_uuid": str(control_conversation.uuid),
            "control_message_id": control_message.id,
        }
        ingest_event.save(update_fields=["result_meta"])
        return str(control_conversation.uuid), control_message.id

    control_message = ControlMessage.objects.create(
        conversation=control_conversation,
        direction="in",
        author_type="transport_user",
        author_label=str(identity),
        text=message_text,
        payload=payload,
        source_transport=transport.key,
        source_conversation_id=conversation.external_conversation_id,
        source_message_id=source_message_id,
    )
    broadcast_control_message(control_message)
    ingest_event.result_meta = {
        "conversation_uuid": str(control_conversation.uuid),
        "control_message_id": control_message.id,
    }
    ingest_event.save(update_fields=["result_meta"])

    if event.kind == "callback":
        from control.handlers import handle_approval_callback

        handle_approval_callback(
            conversation=control_conversation,
            event=event,
            transport_key=transport.key,
            performed_by=event.from_username
            or identity.display_name
            or identity.external_user_id,
        )

    return str(control_conversation.uuid), control_message.id


def _extract_pair_code(text: str) -> Optional[str]:
    if not text:
        return None
    trimmed = text.strip()
    if not trimmed:
        return None
    parts = trimmed.split(None, 1)
    if len(parts) < 2:
        return None
    command = parts[0].lstrip("/").lower()
    if command != "pair":
        return None
    return parts[1].strip()


def _send_pairing_notification(endpoint: TransportEndpoint, chat_id: str, text: str) -> None:
    try:
        send_telegram_message(endpoint, chat_id, text)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Failed to send pairing notification to chat %s: %s", chat_id, exc, exc_info=exc
        )
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning(
            "Unable to deliver pairing notification to chat %s: %s", chat_id, exc, exc_info=exc
        )


def _create_system_control_message(
    control_conversation: ControlConversation,
    text: str,
    payload: dict[str, object],
    source_transport: str,
    source_conversation_id: str,
    source_message_id: Optional[str],
) -> ControlMessage:
    message = ControlMessage.objects.create(
        conversation=control_conversation,
        direction="system",
        author_type="system",
        author_label="system",
        text=text,
        payload=payload,
        source_transport=source_transport,
        source_conversation_id=source_conversation_id,
        source_message_id=source_message_id,
    )
    broadcast_control_message(message)
    return message


def _maybe_handle_pairing_command(
    endpoint: TransportEndpoint,
    conversation: CommsConversation,
    control_conversation: ControlConversation,
    event: NormalizedEvent,
    message_text: str,
    payload: dict[str, object],
    source_message_id: Optional[str],
) -> Optional[ControlMessage]:
    pair_code = _extract_pair_code(message_text)
    if not pair_code:
        return None

    now = timezone.now()
    pairing = (
        PendingPairing.objects.select_for_update()
        .select_related("agent")
        .filter(
            endpoint=endpoint,
            status=PendingPairing.STATUS_PENDING,
            expires_at__gt=now,
            pair_code__iexact=pair_code,
        )
        .first()
    )

    control_payload = dict(payload)
    control_payload["pairing_code"] = pair_code.upper()
    control_payload["chat_id"] = conversation.external_conversation_id

    if not pairing:
        failure_text = "❌ Invalid or expired pairing code."
        _send_pairing_notification(
            endpoint,
            conversation.external_conversation_id,
            "Pairing code invalid or expired. Request a new code from the setup wizard.",
        )
        return _create_system_control_message(
            control_conversation,
            failure_text,
            control_payload,
            endpoint.transport.key,
            conversation.external_conversation_id,
            source_message_id,
        )

    pairing.mark_claimed(conversation.external_conversation_id)
    agent = pairing.agent
    if agent:
        agent.default_conversation = control_conversation
        agent.save(update_fields=["default_conversation"])

    agent_label = agent.name if agent else None
    success_text = (
        f"✅ Paired Telegram chat to Agent {agent_label}"
        if agent_label
        else "✅ Paired Telegram chat"
    )
    notification = (
        f"Paired successfully to {agent_label}."
        if agent_label
        else "Telegram chat paired successfully."
    )
    _send_pairing_notification(
        endpoint,
        conversation.external_conversation_id,
        notification,
    )
    success_payload = dict(control_payload)
    if agent_label:
        success_payload["agent"] = agent_label
    return _create_system_control_message(
        control_conversation,
        success_text,
        success_payload,
        endpoint.transport.key,
        conversation.external_conversation_id,
        source_message_id,
    )
