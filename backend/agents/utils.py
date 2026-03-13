from django.urls import reverse

from comms.models import Transport, TransportEndpoint

TELEGRAM_TRANSPORT_KEY = "telegram"


def _get_telegram_transport() -> Transport | None:
    return Transport.objects.filter(key=TELEGRAM_TRANSPORT_KEY).first()


def find_agent_telegram_endpoint(agent) -> TransportEndpoint | None:
    transport = _get_telegram_transport()
    if not transport:
        return None
    default_conversation = getattr(agent, "default_conversation", None)
    if default_conversation is not None:
        comms_conversation = getattr(default_conversation, "comms_conversation", None)
        if (
            comms_conversation is not None
            and comms_conversation.transport_id == transport.id
            and comms_conversation.endpoint_id
        ):
            return comms_conversation.endpoint
    pending_pairing = (
        transport.endpoints.filter(pairings__agent=agent)
        .order_by("-pairings__created_at", "-id")
        .first()
    )
    if pending_pairing is not None:
        return pending_pairing
    return (
        TransportEndpoint.objects.filter(transport=transport, kind="bot")
        .order_by("-id")
        .first()
    )


def build_transport_status(agent, endpoint: TransportEndpoint | None = None) -> dict:
    if not endpoint:
        endpoint = find_agent_telegram_endpoint(agent)
    connected = False
    detail = "Telegram is not connected yet."
    cta_label = "Connect Telegram"
    if endpoint and endpoint.config:
        connected = True
        cta_label = "Configure Telegram"
        config = endpoint.config
        parts: list[str] = []
        username = config.get("bot_username")
        display_name = config.get("bot_name")
        if username:
            parts.append("@" + username)
        if display_name:
            parts.append(display_name)
        if parts:
            detail = "Telegram connected to " + " ".join(parts)
        else:
            detail = "Telegram connection established."
    return dict(
        connected=connected,
        detail=detail,
        cta_url=reverse(
            "ui:connect_telegram", kwargs=dict(agent_uuid=agent.id)
        ),
        cta_label=cta_label,
    )
