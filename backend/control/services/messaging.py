from __future__ import annotations

import logging
from typing import Dict

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from control.models import ControlMessage

logger = logging.getLogger(__name__)


def _serialize_message(message: ControlMessage) -> Dict[str, str]:
    return {
        "id": str(message.id),
        "conversation": str(message.conversation.uuid),
        "direction": message.direction,
        "author_type": message.author_type,
        "author_label": message.author_label,
        "text": message.text,
        "payload": message.payload or {},
        "created_at": message.created_at.isoformat(),
    }


def broadcast_control_message(message: ControlMessage) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    group_name = f"control_chat_{message.conversation.uuid}"
    payload = _serialize_message(message)
    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "control.chat.message",
                "message": payload,
            },
        )
    except Exception as exc:
        logger.debug("control chat broadcast failed: %s", exc)
