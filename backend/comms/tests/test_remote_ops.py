from datetime import timedelta

import pytest

from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from comms.models import CommsConversation, RemoteApprovalTicket, Transport, TransportEndpoint
from comms.services.remote_ops import (
    create_remote_approval_ticket,
    handle_remote_callback,
    notify_remote_approval_ticket,
    resolve_remote_ticket_for_code,
)
from comms.transports.base import NormalizedEvent
from core.models import Workspace, WorkspaceMembership
from control.models import ControlConversation
from runs.models import AgentRun, AgentStep
from tools.models import Tool, ToolCall, ToolDefinition, ToolGroup, ToolRisk



def _build_remote_ops_env(suffix: str):
    user = get_user_model().objects.create_user(username=f"remoteops-{suffix}", password="x")
    workspace = Workspace.objects.create(name=f"Remote Ops WS {suffix}")
    WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role=WorkspaceMembership.Role.OWNER,
    )
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        name=f"Remote Ops Agent {suffix}",
        soul="Remote ops tests",
        created_by=user,
    )
    control_conversation = ControlConversation.objects.create(kind="comms_mirror", title=f"Ops {suffix}")
    agent.default_conversation = control_conversation
    agent.save(update_fields=["default_conversation"])
    transport = Transport.objects.create(key=f"telegram-{suffix}", display_name="Telegram")
    endpoint = TransportEndpoint.objects.create(transport=transport, kind="bot", config={"allow_user_ids": ["42"]})
    conversation = CommsConversation.objects.create(
        transport=transport,
        endpoint=endpoint,
        external_conversation_id=f"chat-{suffix}",
        control_conversation=control_conversation,
    )
    group = ToolGroup.objects.create(name=f"RemoteOpsGroup {suffix}")
    tool = Tool.objects.create(
        name=f"file_write_{suffix}",
        tool_group=group,
        risk=ToolRisk.SAFE,
        requires_approval=True,
        released=True,
    )
    ToolDefinition.objects.create(workspace=workspace, tool=tool, enabled=True)
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        started_by=user,
        status=AgentRun.Status.WAITING_FOR_APPROVAL,
        input_text="remote ops",
    )
    step = AgentStep.objects.create(
        run=run,
        step_index=1,
        kind=AgentStep.Kind.TOOL_CALL,
        payload={},
    )
    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name="file_write",
        args={"path": f"notes/{suffix}.txt", "content": "hello"},
        requires_approval=True,
        status=ToolCall.Status.PENDING_APPROVAL,
        correlation_id=step.correlation_id,
    )
    return conversation, control_conversation, tool_call


@pytest.mark.django_db
def test_create_remote_approval_ticket_and_notify(monkeypatch):
    conversation, _control_conversation, tool_call = _build_remote_ops_env("notify")
    sent = {}

    def fake_send(conversation_arg, text, **kwargs):
        sent["conversation"] = conversation_arg
        sent["text"] = text
        sent["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": 321}}}

    monkeypatch.setattr("comms.services.remote_ops.send_conversation_message", fake_send)

    ticket = create_remote_approval_ticket(tool_call)
    assert ticket is not None

    notify_remote_approval_ticket(str(ticket.id))
    ticket.refresh_from_db()

    assert len(ticket.short_code) == 4
    assert ticket.external_message_id == "321"
    assert sent["conversation"] == conversation
    assert "Approval" in sent["text"]
    reply_markup = sent["kwargs"]["reply_markup"]
    assert reply_markup["inline_keyboard"][0][0]["text"] == "Approve"
    assert reply_markup["inline_keyboard"][0][1]["text"] == "Deny"
    assert reply_markup["inline_keyboard"][-2][0]["text"] == "Status"
    assert reply_markup["inline_keyboard"][-2][1]["text"] == "Pause"


@pytest.mark.django_db
def test_resolve_remote_ticket_for_code_accepts_unique_prefix():
    conversation, _control_conversation, tool_call = _build_remote_ops_env("prefix")
    first = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        short_code="4F7K",
        callback_token="tokprefix1",
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    second = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        short_code="9ABC",
        callback_token="tokprefix2",
        expires_at=first.expires_at,
    )

    resolved, error = resolve_remote_ticket_for_code(conversation, "4F")

    assert error is None
    assert resolved == first
    assert second.short_code == "9ABC"


@pytest.mark.django_db
def test_handle_remote_callback_marks_ticket_approved(monkeypatch):
    conversation, control_conversation, tool_call = _build_remote_ops_env("callback")
    ticket = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        short_code="4F7K",
        callback_token="tokcallback1",
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    called = {}

    def fake_approve_tool_call(*, tool_call_id, user, grant_mode, actor_label=None):
        called["tool_call_id"] = tool_call_id
        called["grant_mode"] = grant_mode
        called["actor_label"] = actor_label
        tool_call.status = ToolCall.Status.QUEUED
        tool_call.save(update_fields=["status", "updated_at"])
        return tool_call

    monkeypatch.setattr("tools.services.approvals.approve_tool_call", fake_approve_tool_call)

    event = NormalizedEvent(
        kind="callback",
        update_id=77,
        chat_id=conversation.external_conversation_id,
        from_user_id="42",
        from_username="scott",
        text=None,
        message_id="100",
        callback_data=f"rops:a:{ticket.callback_token}:approve",
        callback_query_id="cq-1",
        ts=1,
    )

    message = handle_remote_callback(
        conversation=control_conversation,
        event=event,
        transport_key=conversation.transport.key,
        performed_by="scott",
        external_user_id="42",
    )

    ticket.refresh_from_db()
    assert message is not None
    assert ticket.status == RemoteApprovalTicket.STATUS_APPROVED
    assert called["tool_call_id"] == str(tool_call.id)
    assert called["actor_label"] == "scott"
