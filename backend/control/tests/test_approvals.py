from datetime import timedelta

import pytest
from django.utils import timezone

from comms.transports.base import NormalizedEvent
from control.handlers import handle_approval_callback
from control.models import ApprovalRequest, ControlConversation
from control.services.approvals import decide_approval


@pytest.mark.django_db
def test_decide_approval_creates_grant():
    request = ApprovalRequest.objects.create(
        tool_name="repo_tree",
        summary="run repo tree",
        payload_preview={},
        constraints={},
    )
    request, grant = decide_approval(str(request.uuid), "approve_once")
    assert request.status == ApprovalRequest.STATUS_APPROVED
    assert grant.scope == "repo_tree"
    assert not grant.is_persistent


@pytest.mark.django_db
def test_decide_approval_for_duration_sets_expiry():
    request = ApprovalRequest.objects.create(
        tool_name="shell_exec",
        summary="timeout",
        payload_preview={},
        constraints={},
    )
    _, grant = decide_approval(str(request.uuid), "approve_for", duration="15m")
    assert isinstance(grant.expires_at, timezone.datetime)
    assert grant.expires_at - timezone.now() <= timedelta(minutes=15) + timedelta(seconds=1)


@pytest.mark.django_db
def test_decide_approval_denies_request():
    request = ApprovalRequest.objects.create(
        tool_name="file_write",
        summary="write",
        payload_preview={},
        constraints={},
    )
    request, grant = decide_approval(str(request.uuid), "deny")
    assert request.status == ApprovalRequest.STATUS_DENIED
    assert grant is None


@pytest.mark.django_db
def test_handle_approval_callback_creates_system_message():
    conversation = ControlConversation.objects.create(kind="comms_mirror", title="test")
    request = ApprovalRequest.objects.create(
        tool_name="file_read",
        summary="read",
        payload_preview={},
        constraints={},
    )
    event = NormalizedEvent(
        kind="callback",
        update_id=1,
        chat_id="chat-1",
        from_user_id="42",
        from_username="alice",
        text=None,
        message_id=None,
        callback_data=f"approve_once:{request.uuid}",
        callback_query_id="cq-1",
        ts=int(timezone.now().timestamp()),
    )
    message = handle_approval_callback(
        conversation,
        event,
        transport_key="telegram",
        performed_by="alice",
    )
    assert message.direction == "system"
    assert "approved" in message.text
    request.refresh_from_db()
    assert request.status == ApprovalRequest.STATUS_APPROVED
