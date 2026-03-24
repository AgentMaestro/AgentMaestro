from __future__ import annotations

from typing import Optional

import httpx

from agents.models import Agent
from comms.models import CommsConversation, PendingPairing
from comms.services.outbound import send_conversation_message
from comms.services.telegram_markup import render_mirror_telegram_html
from logging_utils import get_app_logger
from runs.models import AgentRun
from runs.services.event_builders import build_chat_message_payload
from runs.services.events import append_event

logger = get_app_logger(__name__)

ACTIVE_TRANSPORT_RUN_STATUSES = {
    AgentRun.Status.RUNNING,
    AgentRun.Status.PAUSED,
    AgentRun.Status.WAITING_FOR_USER,
    AgentRun.Status.WAITING_FOR_APPROVAL,
    AgentRun.Status.WAITING_FOR_TOOL,
    AgentRun.Status.WAITING_FOR_SUBRUN,
}


def _telegram_text_chunks(text: str, limit: int = 3500) -> list[str]:
    candidate = (text or "").strip()
    if not candidate:
        return []
    if len(candidate) <= limit:
        return [candidate]
    chunks: list[str] = []
    remaining = candidate
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if not chunk:
            chunk = remaining[:limit].strip()
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _format_http_error(exc: Exception) -> str:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response is None:
        return str(exc)
    try:
        payload = exc.response.json()
        description = payload.get("description") or payload.get("detail")
    except ValueError:
        description = None
    return description or exc.response.text or str(exc)


def _telegram_author_label(author_label: str, agent_name: str) -> str:
    candidate = str(author_label or '').strip()
    agent_label = str(agent_name or 'assistant').strip().lower() or 'assistant'
    if not candidate:
        return agent_label
    lowered = candidate.lower()
    if lowered == 'assistant':
        return agent_label
    if lowered == 'system':
        return 'system'
    return lowered


def _render_telegram_message(*, text: str, author_label: str, agent_name: str) -> str:
    # Keep the first pass intentionally simple: Telegram HTML with a small
    # Markdown-to-HTML mapping that prioritizes readability.
    return render_mirror_telegram_html(
        author_label=_telegram_author_label(author_label, agent_name),
        body=text or "",
    )


def paired_agent_for_conversation(conversation: CommsConversation):
    pairing = (
        PendingPairing.objects.filter(
            endpoint=conversation.endpoint,
            claimed_chat_id=conversation.external_conversation_id,
            status=PendingPairing.STATUS_CLAIMED,
            agent__isnull=False,
        )
        .select_related("agent")
        .order_by("-claimed_at", "-created_at")
        .first()
    )
    if pairing is None:
        control_conversation = getattr(conversation, "control_conversation", None)
        if control_conversation is not None:
            return (
                Agent.objects.filter(default_conversation=control_conversation)
                .select_related("default_conversation")
                .order_by("-created_at")
                .first()
            )
        return None
    return pairing.agent


def paired_conversation_for_agent(agent) -> CommsConversation | None:
    default_conversation = getattr(agent, "default_conversation", None)
    if default_conversation is not None:
        comms_conversation = getattr(default_conversation, "comms_conversation", None)
        if (
            comms_conversation is not None
            and getattr(getattr(comms_conversation, "transport", None), "key", "") == "telegram"
            and comms_conversation.transport_id
            and comms_conversation.endpoint_id
        ):
            return comms_conversation
    pairing = (
        PendingPairing.objects.filter(
            agent=agent,
            status=PendingPairing.STATUS_CLAIMED,
            claimed_chat_id__isnull=False,
            endpoint__transport__key="telegram",
        )
        .select_related("endpoint", "endpoint__transport")
        .order_by("-claimed_at", "-created_at")
        .first()
    )
    if pairing is None or not pairing.claimed_chat_id:
        return None
    conversation, _ = CommsConversation.objects.get_or_create(
        endpoint=pairing.endpoint,
        external_conversation_id=pairing.claimed_chat_id,
        defaults={
            "transport": pairing.endpoint.transport,
            "title": f"{agent.name} Telegram",
        },
    )
    return conversation


def active_run_for_agent(agent) -> AgentRun | None:
    dashboard_run = (
        AgentRun.objects.filter(
            agent=agent,
            channel=AgentRun.Channel.DASHBOARD,
            status__in=ACTIVE_TRANSPORT_RUN_STATUSES,
        )
        .order_by("-started_at", "-created_at")
        .first()
    )
    if dashboard_run is not None:
        return dashboard_run
    return (
        AgentRun.objects.filter(agent=agent, status__in=ACTIVE_TRANSPORT_RUN_STATUSES)
        .order_by("-started_at", "-created_at")
        .first()
    )


def forward_transport_user_message(
    *,
    conversation: CommsConversation,
    text: str,
    author_label: str,
    source_transport: str,
    source_message_id: str | None,
) -> tuple[str | None, str | None]:
    agent = paired_agent_for_conversation(conversation)
    if agent is None:
        return None, "This Telegram chat is not paired to an agent."

    run = active_run_for_agent(agent)
    if run is None:
        return None, f"No active {agent.name} chat session is running in the browser right now."

    payload = build_chat_message_payload("user", text)
    payload.update(
        {
            "source_transport": source_transport,
            "source_message_id": source_message_id or "",
            "author_label": author_label,
        }
    )
    append_event(
        run_id=str(run.id),
        event_type="chat_message",
        payload=payload,
        broadcast_to_run=True,
        correlation_id=run.correlation_id,
    )
    return str(run.id), None


def send_run_transport_message(
    *,
    run_id: str,
    text: str,
    author_label: str,
    control_payload: Optional[dict[str, object]] = None,
    mirror_to_control: bool = False,
    **kwargs: object,
) -> bool:
    try:
        run = AgentRun.objects.select_related("agent").get(id=run_id)
    except AgentRun.DoesNotExist:
        return False
    conversation = paired_conversation_for_agent(run.agent)
    if conversation is None:
        return False
    chunks = _telegram_text_chunks(text)
    telegram_author = _telegram_author_label(author_label, getattr(run.agent, 'name', 'Assistant'))
    explicit_parse_mode = kwargs.get("parse_mode")
    try:
        for index, chunk in enumerate(chunks or [text], start=1):
            if explicit_parse_mode:
                rendered_chunk = chunk
                send_kwargs = dict(kwargs)
            else:
                rendered_chunk = _render_telegram_message(
                    text=chunk,
                    author_label=telegram_author,
                    agent_name=getattr(run.agent, 'name', 'Assistant'),
                )
                send_kwargs = dict(kwargs)
                send_kwargs["parse_mode"] = "HTML"
            send_conversation_message(
                conversation,
                rendered_chunk,
                actor_label=telegram_author,
                author_type="system",
                control_direction="system",
                control_payload=control_payload,
                mirror_to_control=mirror_to_control,
                **send_kwargs,
            )
            if len(chunks) > 1:
                logger.info(
                    "Sent Telegram chunk %s/%s run=%s agent=%s len=%s",
                    index,
                    len(chunks),
                    run_id,
                    getattr(run.agent, "slug", None),
                    len(chunk),
                )
    except Exception as exc:
        logger.exception(
            "Failed sending transport message run=%s agent=%s detail=%s",
            run_id,
            getattr(run.agent, "slug", None),
            _format_http_error(exc),
        )
        return False
    return True
