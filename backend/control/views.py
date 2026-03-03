from __future__ import annotations

from typing import Iterable, Optional

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

import httpx

from agents.models import Agent
from comms.models import PendingPairing, Transport, TransportEndpoint
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
        key="telegram", defaults={"display_name": "Telegram"}
    )
    endpoint = _find_agent_endpoint(agent, transport)
    bot_info = _bot_info_from_config(endpoint)
    allow_user_value = _allow_user_ids_value(endpoint)
    pairing = None
    pairing_status_url = None

    if bot_info and endpoint:
        pairing = _pending_pairing_for_agent(agent, endpoint)
        pairing_status_url = reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid})

    if request.method == "POST":
        bot_token_env = (request.POST.get("bot_token_env") or "").strip() or "TELEGRAM_BOT_TOKEN"
        raw_token = (request.POST.get("bot_token") or "").strip()
        allow_user_input = request.POST.get("allow_user_ids", "")
        allow_user_ids = _parse_allow_user_ids(allow_user_input)
        allow_user_value = ", ".join(allow_user_ids)

        if not endpoint:
            endpoint = TransportEndpoint(transport=transport, kind="bot")
        config = dict(endpoint.config or {})
        config.update(
            {
                "bot_token_env": bot_token_env,
                "allow_user_ids": allow_user_ids,
                "agent_id": str(agent.id),
            }
        )
        if raw_token:
            config["bot_token"] = raw_token
        endpoint.config = config
        endpoint.kind = "bot"
        endpoint.save()

        try:
            bot_data = get_telegram_bot_info(endpoint)
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
            ).strip()
            bot_display_name = bot_display_name or bot_username
            config.update(
                {
                    "bot_id": bot_data.get("id"),
                    "bot_username": bot_username,
                    "bot_name": bot_display_name,
                }
            )
            endpoint.config = config
            endpoint.save(update_fields=["config"])
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

    if bot_info and endpoint and not pairing:
        pairing = _pending_pairing_for_agent(agent, endpoint)
        pairing_status_url = reverse("ui:pairing_status", kwargs={"pairing_uuid": pairing.uuid})

    context = {
        "agent": agent,
        "bot_info": bot_info,
        "pairing": pairing,
        "pairing_status_url": pairing_status_url,
        "allow_user_ids": allow_user_value,
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
        control_uuid = pairing.agent.default_conversation.uuid
        data["control_conversation_uuid"] = str(control_uuid)
        data["redirect_url"] = f"/ui/chat/{control_uuid}/"
    return JsonResponse(data)


def _find_agent_endpoint(agent: Agent, transport: Transport) -> Optional[TransportEndpoint]:
    for endpoint in TransportEndpoint.objects.filter(transport=transport, kind="bot"):
        config = endpoint.config or {}
        if str(config.get("agent_id")) == str(agent.id):
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
