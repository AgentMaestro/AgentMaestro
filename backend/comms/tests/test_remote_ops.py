from datetime import timedelta

import pytest

from django.contrib.auth import get_user_model
from django.utils import timezone

from agents.models import Agent
from comms.models import CommsConversation, RemoteApprovalTicket, Transport, TransportEndpoint
from comms.services.remote_ops import (
    create_remote_approval_ticket,
    expire_remote_approval_ticket,
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
    assert reply_markup["inline_keyboard"][-2][1]["text"] == "Cancel"


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
        external_message_id="100",
        short_code="4F7K",
        callback_token="tokcallback1",
        web_url="http://example.test/run/1",
        summary={"tool_name": "file_write", "args": {"path": "notes/callback.txt"}, "agent_name": tool_call.run.agent.name},
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    called = {}
    edits = {}
    events = []

    def fake_approve_tool_call(*, tool_call_id, user, grant_mode, actor_label=None):
        called["tool_call_id"] = tool_call_id
        called["grant_mode"] = grant_mode
        called["actor_label"] = actor_label
        tool_call.status = ToolCall.Status.QUEUED
        tool_call.save(update_fields=["status", "updated_at"])
        return tool_call

    def fake_edit(conversation_arg, message_id, text, **kwargs):
        edits["conversation"] = conversation_arg
        edits["message_id"] = message_id
        edits["text"] = text
        edits["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": message_id}}}

    def fake_append_event(*, run_id, event_type, payload, correlation_id=None, broadcast_to_run=True, metadata=None):
        events.append({"run_id": run_id, "event_type": event_type, "payload": payload})

    monkeypatch.setattr("tools.services.approvals.approve_tool_call", fake_approve_tool_call)
    monkeypatch.setattr("comms.services.remote_ops.edit_conversation_message", fake_edit)
    monkeypatch.setattr("comms.services.remote_ops.append_event", fake_append_event)

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
    assert edits["conversation"] == conversation
    assert edits["message_id"] == "100"
    assert "APPROVED" in edits["text"]
    assert edits["kwargs"]["reply_markup"]["inline_keyboard"] == [[{"text": "Open Run", "url": ticket.web_url}]]
    assert events[-1]["event_type"] == "remote_ops_message"
    assert events[-1]["payload"]["text"] == "Approved 4F7K."
    assert events[-1]["payload"]["author_label"] == "scott"


@pytest.mark.django_db
def test_handle_remote_callback_is_idempotent_when_tool_call_already_processed(monkeypatch):
    conversation, control_conversation, tool_call = _build_remote_ops_env("callback-repeat")
    ticket = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        external_message_id="101",
        short_code="8JKL",
        callback_token="tokcallbackrepeat1",
        web_url="http://example.test/run/repeat",
        summary={"tool_name": "file_write", "args": {"path": "notes/repeat.txt"}, "agent_name": tool_call.run.agent.name},
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    tool_call.status = ToolCall.Status.QUEUED
    tool_call.save(update_fields=["status", "updated_at"])
    edits = {}

    def fake_edit(conversation_arg, message_id, text, **kwargs):
        edits["conversation"] = conversation_arg
        edits["message_id"] = message_id
        edits["text"] = text
        edits["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": message_id}}}

    def fail_approve_tool_call(**kwargs):
        raise AssertionError("approve_tool_call should not run for already-processed tool calls")

    monkeypatch.setattr("comms.services.remote_ops.edit_conversation_message", fake_edit)
    monkeypatch.setattr("tools.services.approvals.approve_tool_call", fail_approve_tool_call)

    event = NormalizedEvent(
        kind="callback",
        update_id=78,
        chat_id=conversation.external_conversation_id,
        from_user_id="42",
        from_username="scott",
        text=None,
        message_id="101",
        callback_data=f"rops:a:{ticket.callback_token}:approve",
        callback_query_id="cq-repeat",
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
    assert ticket.action_error == "tool_call_already_acted_on"
    assert edits["conversation"] == conversation
    assert edits["message_id"] == "101"
    assert "APPROVED" in edits["text"]


@pytest.mark.django_db
def test_expire_remote_approval_ticket_updates_card_and_emits_run_event(monkeypatch):
    conversation, _control_conversation, tool_call = _build_remote_ops_env("expire")
    ticket = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        external_message_id="200",
        short_code="9ABC",
        callback_token="tokexpire1",
        web_url="http://example.test/run/2",
        summary={"tool_name": "file_write", "args": {"path": "notes/expire.txt"}, "agent_name": tool_call.run.agent.name},
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    edits = {}
    events = []
    denied = {}

    def fake_deny_tool_call(*, tool_call_id, user, reason, actor_label=None):
        denied["tool_call_id"] = tool_call_id
        denied["reason"] = reason
        denied["actor_label"] = actor_label
        tool_call.status = ToolCall.Status.DENIED
        tool_call.save(update_fields=["status", "updated_at"])
        return tool_call

    def fake_edit(conversation_arg, message_id, text, **kwargs):
        edits["conversation"] = conversation_arg
        edits["message_id"] = message_id
        edits["text"] = text
        edits["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": message_id}}}

    def fake_append_event(*, run_id, event_type, payload, correlation_id=None, broadcast_to_run=True, metadata=None):
        events.append({"run_id": run_id, "event_type": event_type, "payload": payload})

    monkeypatch.setattr("tools.services.approvals.deny_tool_call", fake_deny_tool_call)
    monkeypatch.setattr("comms.services.remote_ops.edit_conversation_message", fake_edit)
    monkeypatch.setattr("comms.services.remote_ops.append_event", fake_append_event)

    expire_remote_approval_ticket(ticket, acted_by_label="system")

    ticket.refresh_from_db()
    assert ticket.status == RemoteApprovalTicket.STATUS_EXPIRED
    assert denied["tool_call_id"] == str(tool_call.id)
    assert denied["reason"] == "Approval expired"
    assert "EXPIRED" in edits["text"]
    assert "Timed out at" in edits["text"]
    assert events[-1]["event_type"] == "remote_ops_message"
    assert events[-1]["payload"]["text"] == "Approval 9ABC timed out."
    assert events[-1]["payload"]["author_label"] == "system"


@pytest.mark.django_db
def test_expire_remote_approval_ticket_reconciles_tool_call_already_resolved(monkeypatch):
    conversation, _control_conversation, tool_call = _build_remote_ops_env("reconcile")
    ticket = RemoteApprovalTicket.objects.create(
        workspace=tool_call.run.workspace,
        run=tool_call.run,
        tool_call=tool_call,
        transport=conversation.transport,
        endpoint=conversation.endpoint,
        conversation=conversation,
        external_chat_id=conversation.external_conversation_id,
        external_message_id="300",
        short_code="7XYZ",
        callback_token="tokreconcile1",
        web_url="http://example.test/run/3",
        summary={"tool_name": "file_write", "args": {"path": "notes/reconcile.txt"}, "agent_name": tool_call.run.agent.name},
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    tool_call.status = ToolCall.Status.QUEUED
    tool_call.save(update_fields=["status", "updated_at"])
    edits = {}

    def fake_edit(conversation_arg, message_id, text, **kwargs):
        edits["conversation"] = conversation_arg
        edits["message_id"] = message_id
        edits["text"] = text
        edits["kwargs"] = kwargs
        return {"response": {"ok": True, "result": {"message_id": message_id}}}

    def fail_deny_tool_call(**kwargs):
        raise AssertionError("deny_tool_call should not run for already-resolved tool calls")

    monkeypatch.setattr("comms.services.remote_ops.edit_conversation_message", fake_edit)
    monkeypatch.setattr("tools.services.approvals.deny_tool_call", fail_deny_tool_call)

    expire_remote_approval_ticket(ticket, acted_by_label="system")

    ticket.refresh_from_db()
    assert ticket.status == RemoteApprovalTicket.STATUS_APPROVED
    assert ticket.action_error == "Resolved before remote approval expiry task ran."
    assert edits["conversation"] == conversation
    assert edits["message_id"] == "300"
    assert "APPROVED" in edits["text"]
