from __future__ import annotations

from typing import Iterable, Optional

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

import httpx

from agents.models import Agent
from comms.models import PendingPairing, Transport, TransportEndpoint, generate_callback_token
from comms.services.outbound import get_telegram_bot_info, send_telegram_text
from control.models import ControlConversation, ControlMessage
from control.services.messaging import broadcast_control_message

logger = logging.getLogger(__name__)


def _format_telegram_error(exc: httpx.HTTPStatusError) -> str:
    if not exc.response:
        return str(exc)
    try:
        payload = exc.response.json()
        description = payload.get("description")
    except ValueError:
        description = None
    detail = description or exc.response.text or exc.response.reason_phrase or str(exc)
    return f"{exc.response.status_code} {detail}"


def _gather_messages(conversation: ControlConversation) -> Iterable[ControlMessage]:
    return conversation.messages.order_by("created_at").all()


def _render_chat(
    request,
    conversations: Iterable[ControlConversation],
    selected: Optional[ControlConversation],
) -> render:
    context: dict[str, object] = {
        "conversations": conversations,
        "selected": selected,
        "messages": _gather_messages(selected) if selected else [],
    }
    return render(request, "control/chat.html", context)


@login_required
def chat_home(request):
    conversations = ControlConversation.objects.order_by("-updated_at").all()
    selected = conversations[0] if conversations else None
    return _render_chat(request, conversations, selected)


@login_required
def chat_detail(request, uuid):
    conversations = ControlConversation.objects.order_by("-updated_at").all()
    selected = get_object_or_404(ControlConversation, uuid=uuid)
    return _render_chat(request, conversations, selected)


@login_required
def telegram_chat(request):
    conversations = (
        ControlConversation.objects.filter(kind="comms_mirror", comms_conversation__transport__key="telegram")
        .order_by("-updated_at")
        .select_related("comms_conversation")
    )
    selected = conversations[0] if conversations else None
    return _render_chat(request, conversations, selected)


@login_required
@require_http_methods(["POST"])
def chat_send(request, uuid):
    conversation = get_object_or_404(ControlConversation, uuid=uuid)
    text = request.POST.get("message", "").strip()
    if not text:
        return redirect(reverse("control:chat_detail", kwargs={"uuid": uuid}))

    message = ControlMessage.objects.create(
        conversation=conversation,
        direction="out",
        author_type="operator",
        author_label=request.user.get_username(),
        text=text,
    )
    broadcast_control_message(message)

    comms = getattr(conversation, "comms_conversation", None)
    if comms and comms.transport.key == "telegram" and comms.external_conversation_id:
        try:
            send_telegram_text(comms, text, actor_label=request.user.get_username())
        except httpx.HTTPStatusError as exc:
            detail = _format_telegram_error(exc)
            logger.warning(
                "Telegram send failed for conversation %s: %s",
                comms.external_conversation_id,
                detail,
                exc_info=exc,
            )
            messages.error(request, f"Telegram send failed: {detail}")

    return redirect(reverse("control:chat_detail", kwargs={"uuid": uuid}))


@login_required
def connect_telegram(request, agent_uuid):
    agent = get_object_or_404(Agent, id=agent_uuid)
    transport, _ = Transport.objects.get_or_create(
        key="telegram", defaults={"display_name": "Telegram", "mode": "both"}
    )
    endpoint = _find_agent_endpoint(agent, transport)
    bot_info = _bot_info_from_config(endpoint)
    allow_user_value = _allow_user_ids_value(endpoint)
    pairing = None
    pairing_status_url = None
    webhook_url = _telegram_webhook_url(endpoint) if endpoint else None
    webhook_secret = _telegram_webhook_secret(endpoint) if endpoint else ""

    if bot_info and endpoint:
        pairing = _pending_pairing_for_agent(agent, endpoint)
        pairing_status_url = reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid})

    if request.method == "POST":
        bot_token_env = (request.POST.get("bot_token_env") or "").strip() or "TELEGRAM_BOT_TOKEN"
        raw_token = (request.POST.get("bot_token") or "").strip()
        allow_user_input = request.POST.get("allow_user_ids", "")
        allow_user_ids = _parse_allow_user_ids(allow_user_input)

        draft_endpoint = endpoint or TransportEndpoint(transport=transport, kind="bot")
        draft_config = dict(draft_endpoint.config or {})
        draft_config.update(
            {
                "bot_token_env": bot_token_env,
                "allow_user_ids": allow_user_ids,
                "webhook_secret": str(draft_config.get("webhook_secret") or generate_callback_token(20)),
            }
        )
        draft_config.pop("agent_id", None)
        if raw_token:
            draft_config["bot_token"] = raw_token
        draft_endpoint.config = draft_config
        draft_endpoint.kind = "bot"

        try:
            bot_data = get_telegram_bot_info(draft_endpoint)
        except httpx.HTTPStatusError as exc:
            detail = _format_telegram_error(exc)
            messages.error(request, f"Unable to validate Telegram token: {detail}")
        else:
            bot_username = bot_data.get("username") or ""
            bot_display_name = " ".join(
                filter(
                    None,
                    [
                        bot_data.get("first_name"),
                        bot_data.get("last_name"),
                    ],
                )
            ).strip() or bot_username
            endpoint = _find_shared_endpoint_for_bot(
                transport=transport,
                bot_id=str(bot_data.get("id") or ""),
                bot_username=bot_username,
                bot_token_env=bot_token_env,
            ) or (endpoint if endpoint and endpoint.pk else TransportEndpoint(transport=transport, kind="bot"))
            config = dict(endpoint.config or {})
            merged_allowed_ids = sorted({*map(str, config.get("allow_user_ids") or []), *allow_user_ids})
            config.update(
                {
                    "bot_token_env": bot_token_env,
                    "allow_user_ids": merged_allowed_ids,
                    "bot_id": bot_data.get("id"),
                    "bot_username": bot_username,
                    "bot_name": bot_display_name,
                    "webhook_secret": str(config.get("webhook_secret") or draft_config.get("webhook_secret") or generate_callback_token(20)),
                }
            )
            config.pop("agent_id", None)
            if raw_token:
                config["bot_token"] = raw_token
            endpoint.kind = "bot"
            endpoint.config = config
            endpoint.save()
            allow_user_value = ", ".join(merged_allowed_ids)
            messages.success(
                request,
                f"Connected to @{bot_username}" if bot_username else "Connected to Telegram bot",
            )
            bot_info = {
                "username": bot_username,
                "name": bot_display_name,
            }
            pairing = _pending_pairing_for_agent(agent, endpoint)
            pairing_status_url = reverse(
                "ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid}
            )
            webhook_url = _telegram_webhook_url(endpoint)
            webhook_secret = _telegram_webhook_secret(endpoint)

    if bot_info and endpoint and not pairing:
        pairing = _pending_pairing_for_agent(agent, endpoint)
        pairing_status_url = reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid})
        webhook_url = _telegram_webhook_url(endpoint)
        webhook_secret = _telegram_webhook_secret(endpoint)

    context = {
        "agent": agent,
        "bot_info": bot_info,
        "pairing": pairing,
        "pairing_status_url": pairing_status_url,
        "allow_user_ids": allow_user_value,
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
    }
    return render(request, "control/connect_telegram.html", context)


@login_required
def pairing_status(request, pairing_uuid):
    pairing = get_object_or_404(PendingPairing, uuid=pairing_uuid)
    now = timezone.now()
    if pairing.status == PendingPairing.STATUS_PENDING and pairing.expires_at <= now:
        pairing.status = PendingPairing.STATUS_EXPIRED
        pairing.save(update_fields=["status"])
    data = {
        "status": pairing.status,
        "expires_at": pairing.expires_at.isoformat(),
        "pair_code": pairing.pair_code,
        "agent_name": pairing.agent.name if pairing.agent else None,
    }
    if (
        pairing.status == PendingPairing.STATUS_CLAIMED
        and pairing.agent
        and pairing.agent.default_conversation
    ):
        data["agent_slug"] = pairing.agent.slug
        data["redirect_url"] = reverse("agents:agent_detail", kwargs={"slug": pairing.agent.slug})
    return JsonResponse(data)


def _find_agent_endpoint(agent: Agent, transport: Transport) -> Optional[TransportEndpoint]:
    default_conversation = getattr(agent, "default_conversation", None)
    if default_conversation is not None:
        comms_conversation = getattr(default_conversation, "comms_conversation", None)
        if (
            comms_conversation is not None
            and comms_conversation.transport_id == transport.id
            and comms_conversation.endpoint_id
        ):
            return comms_conversation.endpoint
    pairing = (
        PendingPairing.objects.filter(agent=agent, endpoint__transport=transport)
        .select_related("endpoint")
        .order_by("-created_at")
        .first()
    )
    if pairing is not None:
        return pairing.endpoint
    return (
        TransportEndpoint.objects.filter(transport=transport, kind="bot")
        .order_by("-id")
        .first()
    )


def _find_shared_endpoint_for_bot(
    *,
    transport: Transport,
    bot_id: str,
    bot_username: str,
    bot_token_env: str,
) -> Optional[TransportEndpoint]:
    endpoints = TransportEndpoint.objects.filter(transport=transport, kind="bot").order_by("-id")
    for endpoint in endpoints:
        config = endpoint.config or {}
        if bot_id and str(config.get("bot_id") or "") == bot_id:
            return endpoint
    for endpoint in endpoints:
        config = endpoint.config or {}
        if bot_username and str(config.get("bot_username") or "") == bot_username:
            return endpoint
    for endpoint in endpoints:
        config = endpoint.config or {}
        if bot_token_env and str(config.get("bot_token_env") or "") == bot_token_env:
            return endpoint
    return None


def _pending_pairing_for_agent(agent: Agent, endpoint: TransportEndpoint) -> PendingPairing:
    now = timezone.now()
    pairing = (
        PendingPairing.objects.filter(agent=agent, endpoint=endpoint)
        .order_by("-created_at")
        .first()
    )
    if pairing:
        if pairing.status == PendingPairing.STATUS_PENDING and pairing.expires_at > now:
            return pairing
        if pairing.status == PendingPairing.STATUS_PENDING:
            pairing.status = PendingPairing.STATUS_EXPIRED
            pairing.save(update_fields=["status"])
    return PendingPairing.objects.create(agent=agent, endpoint=endpoint)


def _parse_allow_user_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _allow_user_ids_value(endpoint: Optional[TransportEndpoint]) -> str:
    if not endpoint:
        return ""
    allowed = endpoint.config.get("allow_user_ids") if endpoint.config else None
    if not allowed:
        return ""
    return ", ".join(str(item) for item in allowed)


def _bot_info_from_config(endpoint: Optional[TransportEndpoint]) -> Optional[dict[str, str]]:
    if not endpoint:
        return None
    config = endpoint.config or {}
    username = config.get("bot_username")
    name = config.get("bot_name")
    if username or name:
        return {"username": username or "", "name": name or username or ""}
    return None


def _telegram_webhook_url(endpoint: Optional[TransportEndpoint]) -> Optional[str]:
    if not endpoint:
        return None
    base_url = getattr(settings, "AGENTMAESTRO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}{reverse('comms:telegram_webhook', kwargs={'endpoint_id': endpoint.id})}"


def _telegram_webhook_secret(endpoint: Optional[TransportEndpoint]) -> str:
    if not endpoint or not endpoint.config:
        return ""
    return str(endpoint.config.get("webhook_secret") or "")
