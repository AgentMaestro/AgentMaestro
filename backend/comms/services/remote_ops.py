from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from comms.models import CommsConversation, RemoteApprovalTicket
from comms.services.agent_chat_bridge import paired_conversation_for_agent
from comms.services.outbound import send_conversation_message
from control.models import ControlConversation, ControlMessage
from control.services.messaging import broadcast_control_message
from tools.services.approval_grants import (
    GRANT_MODE_ONCE,
    GRANT_MODE_PATH_PREFIX,
    GRANT_MODE_REPOSITORY,
)

logger = logging.getLogger(__name__)

APPROVAL_ACTION_APPROVE = "approve"
APPROVAL_ACTION_DENY = "deny"
APPROVAL_ACTION_ALLOW_FOLDER = "allow-folder"
APPROVAL_ACTION_ALLOW_REPO = "allow-repo"
REMOTE_CALLBACK_PREFIX = "rops"
RUN_CONTROL_STATUS = "status"
RUN_CONTROL_PAUSE = "pause"
RUN_CONTROL_RESUME = "resume"
RUN_CONTROL_CANCEL = "cancel"
ACTION_TO_GRANT_MODE = {
    APPROVAL_ACTION_APPROVE: GRANT_MODE_ONCE,
    APPROVAL_ACTION_ALLOW_FOLDER: GRANT_MODE_PATH_PREFIX,
    APPROVAL_ACTION_ALLOW_REPO: GRANT_MODE_REPOSITORY,
}


def _run_url(run_id: str) -> str:
    base_url = getattr(settings, "AGENTMAESTRO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}{reverse('ui:run_detail', kwargs={'run_id': run_id})}"


def _conversation_for_tool_call(tool_call) -> CommsConversation | None:
    run = tool_call.run
    agent = getattr(run, "agent", None)
    if agent is None:
        return None
    return paired_conversation_for_agent(agent)


def _generate_unique_short_code(conversation: CommsConversation) -> str:
    from comms.models import generate_short_code

    for _ in range(32):
        candidate = generate_short_code()
        exists = RemoteApprovalTicket.objects.filter(
            conversation=conversation,
            status=RemoteApprovalTicket.STATUS_PENDING,
            short_code=candidate,
            expires_at__gt=timezone.now(),
        ).exists()
        if not exists:
            return candidate
    raise RuntimeError("Unable to generate a unique remote approval short code")


def _scope_options(tool_call) -> list[dict[str, str]]:
    from tools.services.approvals import grant_options_for_tool_call

    return grant_options_for_tool_call(tool_call)


def _approval_summary(tool_call, *, web_url: str, expires_at) -> dict[str, object]:
    run = tool_call.run
    return {
        "tool_name": tool_call.tool_name,
        "args": tool_call.args or {},
        "run_id": str(run.id),
        "agent_name": getattr(run.agent, "name", ""),
        "workspace_id": str(run.workspace_id),
        "web_url": web_url,
        "expires_at": expires_at.isoformat(),
    }


def _format_args(args: dict[str, object]) -> str:
    if not args:
        return "{}"
    parts: list[str] = []
    for key, value in args.items():
        rendered = str(value)
        if len(rendered) > 80:
            rendered = rendered[:77] + "..."
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def _ticket_message_text(ticket: RemoteApprovalTicket) -> str:
    summary = ticket.summary or {}
    tool_name = summary.get("tool_name") or ticket.tool_call.tool_name
    args = summary.get("args") or ticket.tool_call.args or {}
    agent_name = summary.get("agent_name") or getattr(ticket.run.agent, "name", "")
    expires_local = timezone.localtime(ticket.expires_at).strftime("%H:%M %Z")
    lines = [
        f"Approval {ticket.short_code}",
        f"Tool: {tool_name}",
        f"Args: {_format_args(args)}",
        f"Run: {ticket.run_id}",
    ]
    if agent_name:
        lines.append(f"Agent: {agent_name}")
    lines.append(f"Expires: {expires_local}")
    lines.append("Choose an action below or use approve/deny with the short code.")
    return "\n".join(lines)


def _button(callback_token: str, action: str, label: str) -> dict[str, str]:
    return {"text": label, "callback_data": f"{REMOTE_CALLBACK_PREFIX}:a:{callback_token}:{action}"}


def _run_button(run_id: str, action: str, label: str) -> dict[str, str]:
    return {"text": label, "callback_data": f"{REMOTE_CALLBACK_PREFIX}:r:{run_id}:{action}"}


def _ticket_reply_markup(ticket: RemoteApprovalTicket) -> dict[str, object]:
    rows = [[_button(ticket.callback_token, APPROVAL_ACTION_APPROVE, "Approve"), _button(ticket.callback_token, APPROVAL_ACTION_DENY, "Deny")]]
    option_modes = {option.get("mode") for option in (ticket.scope_options_snapshot or [])}
    scope_row: list[dict[str, str]] = []
    if GRANT_MODE_PATH_PREFIX in option_modes:
        scope_row.append(_button(ticket.callback_token, APPROVAL_ACTION_ALLOW_FOLDER, "Approve Folder"))
    if GRANT_MODE_REPOSITORY in option_modes:
        scope_row.append(_button(ticket.callback_token, APPROVAL_ACTION_ALLOW_REPO, "Approve Repo"))
    if scope_row:
        rows.append(scope_row)
    rows.append([
        _run_button(str(ticket.run_id), RUN_CONTROL_STATUS, "Status"),
        _run_button(str(ticket.run_id), RUN_CONTROL_PAUSE, "Pause"),
        _run_button(str(ticket.run_id), RUN_CONTROL_RESUME, "Resume"),
        _run_button(str(ticket.run_id), RUN_CONTROL_CANCEL, "Cancel"),
    ])
    rows.append([{"text": "Open Run", "url": ticket.web_url}])
    return {"inline_keyboard": rows}


def create_remote_approval_ticket(tool_call) -> RemoteApprovalTicket | None:
    conversation = _conversation_for_tool_call(tool_call)
    if not conversation or not conversation.endpoint_id or not conversation.external_conversation_id:
        return None

    RemoteApprovalTicket.objects.filter(
        tool_call=tool_call,
        status=RemoteApprovalTicket.STATUS_PENDING,
    ).update(status=RemoteApprovalTicket.STATUS_SUPERSEDED, updated_at=timezone.now())

    expires_at = timezone.now() + timedelta(minutes=getattr(settings, "REMOTE_APPROVAL_TTL_MINUTES", 15))
    web_url = _run_url(str(tool_call.run_id))
    ticket = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        short_code=_generate_unique_short_code(conversation),
        summary=_approval_summary(tool_call, web_url=web_url, expires_at=expires_at),
        scope_options_snapshot=_scope_options(tool_call),
        web_url=web_url,
        expires_at=expires_at,
    )
    transaction.on_commit(lambda: notify_remote_approval_ticket(str(ticket.id)))
    return ticket


def notify_remote_approval_ticket(ticket_id: str) -> RemoteApprovalTicket:
    ticket = (
        RemoteApprovalTicket.objects
        .select_related("conversation", "conversation__control_conversation", "endpoint", "tool_call", "run", "run__agent")
        .get(id=ticket_id)
    )
    if not ticket.is_active():
        return ticket
    payload = send_conversation_message(
        ticket.conversation,
        _ticket_message_text(ticket),
        actor_label="remote_ops",
        author_type="system",
        control_direction="system",
        control_payload={"remote_ticket_id": str(ticket.id), "short_code": ticket.short_code},
        mirror_to_control=False,
        reply_markup=_ticket_reply_markup(ticket),
    )
    response = payload.get("response") or {}
    result = response.get("result") if isinstance(response, dict) else {}
    message_id = str((result or {}).get("message_id") or "")
    if message_id and message_id != ticket.external_message_id:
        ticket.external_message_id = message_id
        ticket.save(update_fields=["external_message_id", "updated_at"])
    return ticket


def resolve_remote_ticket_for_code(conversation: CommsConversation, code: str) -> tuple[RemoteApprovalTicket | None, str | None]:
    prefix = (code or "").strip().upper()
    if len(prefix) < 2:
        return None, "Approval codes need at least 2 characters."
    matches = list(
        RemoteApprovalTicket.objects.filter(
            conversation=conversation,
            status=RemoteApprovalTicket.STATUS_PENDING,
            expires_at__gt=timezone.now(),
            short_code__startswith=prefix,
        ).order_by("short_code", "created_at")[:3]
    )
    if not matches:
        return None, f"No active approval matches {prefix}."
    if len(matches) > 1:
        choices = ", ".join(ticket.short_code for ticket in matches)
        return None, f"{prefix} is ambiguous. Matches: {choices}."
    return matches[0], None


def parse_remote_approval_command(text: str) -> tuple[str, str] | None:
    candidate = (text or "").strip()
    if not candidate:
        return None
    parts = candidate.split()
    if len(parts) != 2:
        return None
    action = parts[0].strip().lower()
    code = parts[1].strip()
    if action not in {
        APPROVAL_ACTION_APPROVE,
        APPROVAL_ACTION_DENY,
        APPROVAL_ACTION_ALLOW_FOLDER,
        APPROVAL_ACTION_ALLOW_REPO,
    }:
        return None
    return action, code


def _system_message(
    *,
    control_conversation: ControlConversation,
    text: str,
    transport_key: str,
    source_conversation_id: str,
    source_message_id: str | None,
    payload: dict[str, object],
    author_label: str,
) -> ControlMessage:
    message = ControlMessage.objects.create(
        conversation=control_conversation,
        direction="system",
        author_type="system",
        author_label=author_label,
        text=text,
        payload=payload,
        source_transport=transport_key,
        source_conversation_id=source_conversation_id,
        source_message_id=source_message_id,
    )
    broadcast_control_message(message)
    return message


def _apply_run_control_action(*, run_id: str, action: str, performed_by: str) -> str:
    from runs.models import AgentRun
    from runs.services.recovery import cancel_run, pause_run, resume_run

    run = AgentRun.objects.filter(id=run_id).first()
    if run is None:
        return "Run not found."
    if action == RUN_CONTROL_STATUS:
        return f"Run {run_id} is {run.status}."
    try:
        if action == RUN_CONTROL_PAUSE:
            pause_run(run_id)
            return f"Run {run_id} paused by {performed_by}."
        if action == RUN_CONTROL_RESUME:
            resume_run(run_id)
            return f"Run {run_id} resumed by {performed_by}."
        if action == RUN_CONTROL_CANCEL:
            cancel_run(run_id, reason=f"Canceled via remote ops by {performed_by}")
            return f"Run {run_id} canceled by {performed_by}."
    except Exception as exc:
        logger.warning("Remote run control action failed run=%s action=%s: %s", run_id, action, exc)
        return f"Unable to {action} run {run_id}: {exc}"
    return f"Unsupported run control action: {action}."


def _apply_ticket_action(
    ticket: RemoteApprovalTicket,
    *,
    action: str,
    performed_by: str,
    external_user_id: str,
) -> str:
    from tools.services.approvals import approve_tool_call, deny_tool_call

    if ticket.status != RemoteApprovalTicket.STATUS_PENDING:
        return f"Approval {ticket.short_code} is already {ticket.status}."
    if ticket.expires_at <= timezone.now():
        expire_remote_approval_ticket(ticket, acted_by_external_user_id=external_user_id, acted_by_label=performed_by)
        return f"Approval {ticket.short_code} expired."

    if action == APPROVAL_ACTION_DENY:
        deny_tool_call(
            tool_call_id=str(ticket.tool_call_id),
            user=None,
            reason=f"Denied via {ticket.transport.display_name}",
            actor_label=performed_by,
        )
        ticket.mark_terminal(
            status=RemoteApprovalTicket.STATUS_DENIED,
            acted_by_external_user_id=external_user_id,
            acted_by_label=performed_by,
        )
        return f"Denied {ticket.short_code}."

    grant_mode = ACTION_TO_GRANT_MODE.get(action)
    if not grant_mode:
        return f"Unsupported remote approval action: {action}."

    approve_tool_call(
        tool_call_id=str(ticket.tool_call_id),
        user=None,
        grant_mode=grant_mode,
        actor_label=performed_by,
    )
    ticket.mark_terminal(
        status=RemoteApprovalTicket.STATUS_APPROVED,
        acted_by_external_user_id=external_user_id,
        acted_by_label=performed_by,
    )
    if action == APPROVAL_ACTION_ALLOW_FOLDER:
        return f"Approved {ticket.short_code} for this folder."
    if action == APPROVAL_ACTION_ALLOW_REPO:
        return f"Approved {ticket.short_code} for this repository."
    return f"Approved {ticket.short_code}."


def handle_remote_callback(
    *,
    conversation: ControlConversation,
    event,
    transport_key: str,
    performed_by: str,
    external_user_id: str,
) -> ControlMessage | None:
    callback_data = (event.callback_data or "").strip()
    parts = callback_data.split(":")
    if len(parts) != 4 or parts[0] != REMOTE_CALLBACK_PREFIX:
        return None
    if parts[1] == "r":
        text = _apply_run_control_action(run_id=parts[2], action=parts[3], performed_by=performed_by)
        return _system_message(
            control_conversation=conversation,
            text=text,
            transport_key=transport_key,
            source_conversation_id=event.chat_id,
            source_message_id=event.callback_query_id,
            payload={"callback_data": callback_data, "run_id": parts[2]},
            author_label=performed_by,
        )
    if parts[1] != "a":
        return None
    ticket = (
        RemoteApprovalTicket.objects
        .select_related("conversation", "tool_call", "transport")
        .filter(callback_token=parts[2])
        .first()
    )
    if ticket is None:
        text = "That approval action is no longer valid."
        return _system_message(
            control_conversation=conversation,
            text=text,
            transport_key=transport_key,
            source_conversation_id=event.chat_id,
            source_message_id=event.callback_query_id,
            payload={"callback_data": callback_data, "error": "ticket_not_found"},
            author_label=performed_by,
        )
    text = _apply_ticket_action(
        ticket,
        action=parts[3],
        performed_by=performed_by,
        external_user_id=external_user_id,
    )
    return _system_message(
        control_conversation=conversation,
        text=text,
        transport_key=transport_key,
        source_conversation_id=event.chat_id,
        source_message_id=event.callback_query_id,
        payload={"callback_data": callback_data, "remote_ticket_id": str(ticket.id)},
        author_label=performed_by,
    )


def handle_remote_text_command(
    *,
    conversation: CommsConversation,
    control_conversation: ControlConversation,
    text: str,
    transport_key: str,
    source_message_id: str | None,
    performed_by: str,
    external_user_id: str,
) -> ControlMessage | None:
    parsed = parse_remote_approval_command(text)
    if not parsed:
        return None
    action, code = parsed
    ticket, error = resolve_remote_ticket_for_code(conversation, code)
    if error:
        return _system_message(
            control_conversation=control_conversation,
            text=error,
            transport_key=transport_key,
            source_conversation_id=conversation.external_conversation_id,
            source_message_id=source_message_id,
            payload={"command_text": text, "error": "resolve_failed"},
            author_label=performed_by,
        )
    response_text = _apply_ticket_action(
        ticket,
        action=action,
        performed_by=performed_by,
        external_user_id=external_user_id,
    )
    return _system_message(
        control_conversation=control_conversation,
        text=response_text,
        transport_key=transport_key,
        source_conversation_id=conversation.external_conversation_id,
        source_message_id=source_message_id,
        payload={"command_text": text, "remote_ticket_id": str(ticket.id), "short_code": ticket.short_code},
        author_label=performed_by,
    )


def expire_remote_approval_ticket(
    ticket: RemoteApprovalTicket,
    *,
    acted_by_external_user_id: str = "",
    acted_by_label: str = "system",
) -> RemoteApprovalTicket:
    if ticket.status != RemoteApprovalTicket.STATUS_PENDING:
        return ticket
    from tools.services.approvals import deny_tool_call

    deny_tool_call(
        tool_call_id=str(ticket.tool_call_id),
        user=None,
        reason="Approval expired",
        actor_label=acted_by_label,
    )
    ticket.mark_terminal(
        status=RemoteApprovalTicket.STATUS_EXPIRED,
        acted_by_external_user_id=acted_by_external_user_id,
        acted_by_label=acted_by_label,
    )
    return ticket


def expire_remote_approval_tickets(limit: int = 50) -> int:
    tickets = list(
        RemoteApprovalTicket.objects
        .select_related("tool_call", "transport")
        .filter(status=RemoteApprovalTicket.STATUS_PENDING, expires_at__lte=timezone.now())
        .order_by("expires_at")[:limit]
    )
    count = 0
    for ticket in tickets:
        expire_remote_approval_ticket(ticket)
        count += 1
    return count
