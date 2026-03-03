from typing import Any, Dict

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def publish_delta(run_id: str, text_delta: str, meta: Dict[str, Any] | None = None):
    """
    Minimal hook to push streaming deltas to a Channels group.
    Group name: f\"llm_run_{run_id}\".
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    payload = {"type": "llm.delta", "delta": text_delta, "meta": meta or {}}
    async_to_sync(channel_layer.group_send)(f"llm_run_{run_id}", payload)
