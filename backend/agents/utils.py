from django.urls import reverse

from comms.models import Transport, TransportEndpoint

TELEGRAM_TRANSPORT_KEY = "telegram"


def _get_telegram_transport() -> Transport | None:
    return Transport.objects.filter(key=TELEGRAM_TRANSPORT_KEY).first()


def find_agent_telegram_endpoint(agent) -> TransportEndpoint | None:
    transport = _get_telegram_transport()
    if not transport:
        return None
    agent_id = str(agent.id)
    return (
        TransportEndpoint.objects.filter(
            transport=transport,
            kind="bot",
            config__agent_id=agent_id,
        )
        .order_by("-id")
        .first()
    )


def build_transport_status(agent, endpoint: TransportEndpoint | None = None) -> dict:
    if not endpoint:
        endpoint = find_agent_telegram_endpoint(agent)
    connected = False
    detail = "Telegram bot is not configured for this agent."
    if endpoint and endpoint.config:
        connected = True
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
        cta_label="Configure Telegram",
    )
