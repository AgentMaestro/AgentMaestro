from __future__ import annotations

from typing import Optional

from comms.transports.base import NormalizedEvent
from django.utils import timezone

from control.models import ControlConversation, ControlMessage
from control.services.approvals import decide_approval
from control.services.messaging import broadcast_control_message
from logging_utils import get_app_logger, scrub_sensitive_text, scrub_sensitive_value

logger = get_app_logger(__name__)


def _build_callback_text(decision: str, approval_uuid: str, duration: Optional[str]) -> str:
    if decision == "denied":
        return f"Approval {approval_uuid} was denied."
    if decision == "approve_future":
        return f"Approval {approval_uuid} was approved for future runs."
    if decision == "approve_for":
        return f"Approval {approval_uuid} was approved for {duration}."
    return f"Approval {approval_uuid} was approved."


def handle_approval_callback(
    conversation: ControlConversation,
    event: NormalizedEvent,
    transport_key: Optional[str] = None,
    performed_by: Optional[str] = None,
) -> Optional[ControlMessage]:
    callback_data = (event.callback_data or event.text or "").strip()
    if not callback_data:
        return None

    parts = callback_data.split(":")
    if len(parts) < 2:
        logger.warning("Malformed approval callback: %s", scrub_sensitive_text(callback_data))
        return None

    action = parts[0]
    approval_uuid = parts[1]
    duration = parts[2] if len(parts) > 2 else None
    decision_map = {
        "approve_once": "approve_once",
        "approve_for": "approve_for",
        "approve_future": "approve_future",
        "deny": "deny",
    }
    if action not in decision_map:
        logger.warning("Unknown approval callback action: %s", scrub_sensitive_text(action))
        return None

    try:
        _, _ = decide_approval(
            approval_uuid,
            decision_map[action],
            duration=duration,
            persistent=(action == "approve_future"),
        )
        decision = decision_map[action]
        text = scrub_sensitive_text(_build_callback_text(decision, approval_uuid, duration))
    except Exception as exc:
        logger.exception(
            "Failed to process approval callback %s", scrub_sensitive_text(callback_data)
        )
        text = scrub_sensitive_text(f"Failed to process approval callback: {exc}")

    message = ControlMessage.objects.create(
        conversation=conversation,
        direction="system",
        author_type="system",
        author_label=performed_by or "system",
        text=text,
        payload=scrub_sensitive_value(
            {
                "callback_data": callback_data,
                "handled_at": timezone.now().isoformat(),
            }
        ),
        source_transport=transport_key or "comms",
        source_conversation_id=event.chat_id,
        source_message_id=event.callback_query_id,
    )
    broadcast_control_message(message)
    return message
