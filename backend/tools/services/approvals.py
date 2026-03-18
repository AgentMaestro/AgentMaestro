from __future__ import annotations

from typing import Any, Dict, Optional

import logging
from django.db import transaction
from django.utils import timezone
from runs.models import AgentRun, AgentStep
from runs.services.events import append_event, broadcast_approvals_event
from runs.services.state import transition_run
from runs.services.steps import append_step
from tools.models import ToolApprovalGrant, ToolCall
from tools.services.approval_grants import (
    GRANT_MODE_ONCE,
    active_grants_for_run,
    available_grant_options,
    create_grant_from_tool_call,
    find_matching_grant,
    serialize_grant,
)
from tools.services.command_guardrails import ToolCommandGuardrailError, validate_tool_request
from tools.services.quotas import acquire_tool_call_slots, release_tool_call_slots
from comms.services.remote_ops import create_remote_approval_ticket


logger = logging.getLogger(__name__)

TOOL_CALL_REQUESTED_EVENT = "tool_call_requested"
TOOL_CALL_APPROVED_EVENT = "tool_call_approved"
TOOL_CALL_DENIED_EVENT = "tool_call_denied"
TOOL_CALL_STATUS_EVENT = "tool_call_status"
TOOL_APPROVAL_GRANT_CREATED_EVENT = "tool_approval_grant_created"
TOOL_APPROVAL_GRANT_REVOKED_EVENT = "tool_approval_grant_revoked"
TOOL_APPROVAL_GRANTS_CLEARED_EVENT = "tool_approval_grants_cleared"
TOOL_APPROVAL_GRANTS_UPDATED_EVENT = "tool_approval_grants_updated"


def _tool_display_name(tool_call: ToolCall) -> str:
    label = str((tool_call.args or {}).get("display_label") or "").strip()
    return label or tool_call.tool_name


def _tool_display_summary(tool_call: ToolCall) -> str:
    summary = str((tool_call.args or {}).get("display_summary") or "").strip()
    return summary


def _schedule_approvals_push(*, workspace_id: str, event: str, data: Dict[str, Any]) -> None:
    def _do_broadcast():
        broadcast_approvals_event(workspace_id=workspace_id, event=event, data=data)

    transaction.on_commit(_do_broadcast)


def _broadcast_tool_call_status(tool_call: ToolCall, *, status: Optional[str] = None, reason: Optional[str] = None, detail: Optional[str] = None) -> None:
    payload = {
        "tool_call_id": str(tool_call.id),
        "tool_name": tool_call.tool_name,
        "tool_display_name": _tool_display_name(tool_call),
        "status": status or tool_call.status,
        "requires_approval": tool_call.requires_approval,
        "args": tool_call.args,
        "celery_task_id": tool_call.celery_task_id,
        "approval_metadata": tool_call.approval_metadata or {},
        "display_summary": _tool_display_summary(tool_call),
        "approval_grant_id": str(tool_call.approval_grant_id) if tool_call.approval_grant_id else "",
    }
    if reason:
        payload["error"] = reason
    if detail:
        payload["detail"] = detail
    append_event(
        run_id=str(tool_call.run_id),
        event_type=TOOL_CALL_STATUS_EVENT,
        payload=payload,
        correlation_id=tool_call.correlation_id,
    )


def _publish_terminal_tool_feedback(tool_call: ToolCall, *, payload: dict[str, Any]) -> None:
    from tools.services.execution import _publish_tool_result_ready
    from tools.services.result_bus import store_tool_result

    tool_call.refresh_from_db(fields=["provider_call_id"])
    provider_call_id = str(tool_call.provider_call_id or "").strip() or None
    result_payload = {
        "tool_call_id": str(tool_call.id),
        "status": tool_call.status,
        "tool_name": tool_call.tool_name,
        "tool_display_name": _tool_display_name(tool_call),
        "stdout": tool_call.stdout or "",
        "stderr": tool_call.stderr or "",
        "result": payload,
        "run_id": str(tool_call.run_id),
        "correlation_id": str(tool_call.correlation_id),
        "provider_call_id": provider_call_id,
    }
    store_tool_result(
        run_id=str(tool_call.run_id),
        tool_call_id=str(tool_call.id),
        payload=result_payload,
    )
    _publish_tool_result_ready(
        str(tool_call.run_id),
        str(tool_call.id),
        provider_call_id=provider_call_id,
    )


def _enqueue_and_schedule(tool_call_id: str) -> None:
    tool_call = ToolCall.objects.select_related("run").get(id=tool_call_id)
    tool_call.status = ToolCall.Status.QUEUED
    tool_call.updated_at = timezone.now()
    tool_call.save(update_fields=["status", "updated_at"])
    _broadcast_tool_call_status(tool_call, status=ToolCall.Status.QUEUED)
    from tools.tasks import execute_tool_call_async
    task = execute_tool_call_async.delay(tool_call_id)
    tool_call.celery_task_id = task.id or ""
    tool_call.save(update_fields=["celery_task_id", "updated_at"])


def _schedule_execution_after_commit(tool_call_id: str) -> None:
    transaction.on_commit(lambda: _enqueue_and_schedule(tool_call_id))




def _schedule_headless_approval_denial(tool_call_id: str, reason: str | None) -> None:
    def _finalize() -> None:
        from runs.services.headless import handle_headless_approval_denial
        from tools.models import ToolCall

        tool_call = ToolCall.objects.select_related("run").get(id=tool_call_id)
        handle_headless_approval_denial(tool_call, reason=reason or "headless_approval_denied")

    transaction.on_commit(_finalize)

def _schedule_grants_snapshot_push(*, run: AgentRun) -> None:
    def _do_broadcast():
        broadcast_approvals_event(
            workspace_id=str(run.workspace_id),
            event=TOOL_APPROVAL_GRANTS_UPDATED_EVENT,
            data={
                "run_id": str(run.id),
                "grants": active_grants_for_run(str(run.id)),
            },
        )

    transaction.on_commit(_do_broadcast)


@transaction.atomic
def request_tool_call_approval(
    *,
    run_id: str,
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    requires_approval: bool = True,
) -> ToolCall:
    run = (
        AgentRun.objects.select_for_update()
        .select_related("workspace")
        .get(id=run_id)
    )

    if run.status not in {
        AgentRun.Status.PENDING,
        AgentRun.Status.RUNNING,
        AgentRun.Status.WAITING_FOR_APPROVAL,
    }:
        raise RuntimeError(f"Cannot request tool call from run {run.status}")

    try:
        validate_tool_request(tool_name, args or {})
    except ToolCommandGuardrailError:
        logger.info(
            "Rejected tool request run=%s tool=%s args=%s because a specialized tool should be used",
            run_id,
            tool_name,
            list((args or {}).keys()),
        )
        raise

    step = append_step(
        run_id=run_id,
        kind=AgentStep.Kind.TOOL_CALL,
        payload={"tool_name": tool_name, "args": args or {}},
    )
    approval_grant = None
    approval_metadata: dict[str, Any] = {}
    auto_approved = False
    if requires_approval:
        approval_grant = find_matching_grant(
            run_id=str(run.id),
            tool_name=tool_name,
            args=args or {},
        )
        if approval_grant is not None:
            auto_approved = True
            approval_metadata = {
                "mode": "grant_match",
                "grant_id": str(approval_grant.id),
                "grant_label": approval_grant.metadata.get("label", ""),
                "grant_scope_display": approval_grant.metadata.get("scope_display", ""),
            }

    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name=tool_name,
        args=args or {},
        requires_approval=requires_approval,
        status=ToolCall.Status.PENDING_APPROVAL if requires_approval and not auto_approved else ToolCall.Status.QUEUED,
        correlation_id=step.correlation_id,
        approved_by=approval_grant.created_by if auto_approved else None,
        approved_at=timezone.now() if auto_approved else None,
        approval_grant=approval_grant,
        approval_metadata=approval_metadata,
    )

    if requires_approval and not auto_approved:
        acquire_tool_call_slots(str(run.workspace_id), str(run.id), str(tool_call.id))
        transition_run(run_id=run_id, new_status=AgentRun.Status.WAITING_FOR_APPROVAL)
        create_remote_approval_ticket(tool_call)
        _schedule_approvals_push(
            workspace_id=str(run.workspace_id),
            event=TOOL_CALL_REQUESTED_EVENT,
            data={
                "run_id": str(run.id),
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_call.tool_name,
                "tool_display_name": _tool_display_name(tool_call),
                "status": tool_call.status,
            },
        )
    elif requires_approval and auto_approved:
        _schedule_approvals_push(
            workspace_id=str(run.workspace_id),
            event=TOOL_CALL_APPROVED_EVENT,
            data={
                "run_id": str(run.id),
                "tool_call_id": str(tool_call.id),
                "approved_by": getattr(approval_grant.created_by, "username", None),
                "status": tool_call.status,
                "approved_via": "grant",
                "approval_grant_id": str(approval_grant.id),
            },
        )
        _schedule_execution_after_commit(str(tool_call.id))
    else:
        _schedule_execution_after_commit(str(tool_call.id))

    append_event(
        run_id=run_id,
        event_type=TOOL_CALL_REQUESTED_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "tool_display_name": _tool_display_name(tool_call),
            "display_summary": _tool_display_summary(tool_call),
            "args": tool_call.args,
            "step_index": step.step_index,
            "status": tool_call.status,
            "approval_metadata": tool_call.approval_metadata or {},
        },
        correlation_id=step.correlation_id,
    )

    if requires_approval and auto_approved:
        append_event(
            run_id=run_id,
            event_type=TOOL_CALL_APPROVED_EVENT,
            payload={
                "tool_call_id": str(tool_call.id),
                "approved_by": getattr(approval_grant.created_by, "username", None),
                "status": tool_call.status,
                "approved_via": "grant",
                "approval_grant_id": str(approval_grant.id),
                "approval_metadata": approval_metadata,
            },
            correlation_id=step.correlation_id,
        )

    _broadcast_tool_call_status(tool_call)

    return tool_call


@transaction.atomic
def approve_tool_call(*, tool_call_id: str, user, grant_mode: str = GRANT_MODE_ONCE, actor_label: Optional[str] = None) -> ToolCall:
    tool_call = (
        ToolCall.objects
        .select_for_update()
        .select_related("run", "run__workspace")
        .get(id=tool_call_id)
    )

    if not tool_call.requires_approval:
        raise RuntimeError("Tool call does not require approval")
    if tool_call.status != ToolCall.Status.PENDING_APPROVAL:
        raise RuntimeError("Tool call already acted on")

    approval_grant = None
    approval_metadata: dict[str, Any] = {"mode": "once"}
    if grant_mode != GRANT_MODE_ONCE:
        approval_grant = create_grant_from_tool_call(tool_call, user, grant_mode)
        if approval_grant is None:
            raise RuntimeError("Unsupported temporary approval scope for this tool call")
        approval_metadata = {
            "mode": "grant_create",
            "grant_id": str(approval_grant.id),
            "grant_label": approval_grant.metadata.get("label", ""),
            "grant_scope_display": approval_grant.metadata.get("scope_display", ""),
        }
    tool_call.mark_approved(
        user,
        approval_grant=approval_grant,
        approval_metadata=approval_metadata,
    )
    tool_call.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "approval_grant",
            "approval_metadata",
            "updated_at",
        ]
    )

    if approval_grant is not None:
        append_event(
            run_id=str(tool_call.run_id),
            event_type=TOOL_APPROVAL_GRANT_CREATED_EVENT,
            payload={
                "grant": serialize_grant(approval_grant),
                "tool_call_id": str(tool_call.id),
            },
            correlation_id=tool_call.correlation_id,
        )
        _schedule_grants_snapshot_push(run=tool_call.run)

    append_event(
        run_id=str(tool_call.run_id),
        event_type=TOOL_CALL_APPROVED_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "tool_display_name": _tool_display_name(tool_call),
            "display_summary": _tool_display_summary(tool_call),
            "approved_by": getattr(user, "username", None) or actor_label,
            "status": tool_call.status,
            "approval_metadata": approval_metadata,
            "approval_grant_id": str(approval_grant.id) if approval_grant else "",
        },
        correlation_id=tool_call.correlation_id,
    )

    release_tool_call_slots(
        str(tool_call.run.workspace_id),
        str(tool_call.run_id),
        str(tool_call.id),
    )

    transition_run(run_id=str(tool_call.run_id), new_status=AgentRun.Status.RUNNING)

    _schedule_approvals_push(
        workspace_id=str(tool_call.run.workspace_id),
        event=TOOL_CALL_APPROVED_EVENT,
        data={
            "run_id": str(tool_call.run_id),
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "tool_display_name": _tool_display_name(tool_call),
            "display_summary": _tool_display_summary(tool_call),
            "approved_by": getattr(user, "username", None) or actor_label,
            "status": tool_call.status,
            "approval_metadata": approval_metadata,
            "approval_grant_id": str(approval_grant.id) if approval_grant else "",
        },
    )

    _broadcast_tool_call_status(
        tool_call,
        status=ToolCall.Status.QUEUED,
        detail=(f"Approved by {getattr(user, 'username', None) or actor_label}." if (getattr(user, 'username', None) or actor_label) else "Approved."),
    )

    _schedule_execution_after_commit(str(tool_call.id))

    return tool_call


@transaction.atomic
def deny_tool_call(*, tool_call_id: str, user, reason: Optional[str] = None, actor_label: Optional[str] = None) -> ToolCall:
    tool_call = (
        ToolCall.objects
        .select_for_update()
        .select_related("run", "run__workspace")
        .get(id=tool_call_id)
    )

    if tool_call.status != ToolCall.Status.PENDING_APPROVAL:
        raise RuntimeError("Tool call not awaiting approval")

    tool_call.status = ToolCall.Status.DENIED
    tool_call.error = reason or "Denied"
    tool_call.observed_at = timezone.now()
    tool_call.save(update_fields=["status", "error", "observed_at", "updated_at"])

    release_tool_call_slots(
        str(tool_call.run.workspace_id),
        str(tool_call.run_id),
        str(tool_call.id),
    )

    append_event(
        run_id=str(tool_call.run_id),
        event_type=TOOL_CALL_DENIED_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "tool_display_name": _tool_display_name(tool_call),
            "display_summary": _tool_display_summary(tool_call),
            "denied_by": getattr(user, "username", None) or actor_label,
            "status": tool_call.status,
            "reason": reason,
        },
        correlation_id=tool_call.correlation_id,
    )

    transition_run(run_id=str(tool_call.run_id), new_status=AgentRun.Status.RUNNING)

    _schedule_approvals_push(
        workspace_id=str(tool_call.run.workspace_id),
        event=TOOL_CALL_DENIED_EVENT,
        data={
            "run_id": str(tool_call.run_id),
            "tool_call_id": str(tool_call.id),
            "denied_by": getattr(user, "username", None) or actor_label,
            "status": tool_call.status,
        },
    )

    _broadcast_tool_call_status(tool_call, status=ToolCall.Status.DENIED, reason=reason)

    if tool_call.tool_name == "scheduled_headless_run_gate":
        _schedule_headless_approval_denial(str(tool_call.id), reason)

    transaction.on_commit(
        lambda: _publish_terminal_tool_feedback(
            tool_call,
            payload={
                "ok": False,
                "error": {
                    "code": "tool_runner.DENIED",
                    "message": reason or "Denied",
                    "details": {
                        "tool_call_id": str(tool_call.id),
                        "tool_name": tool_call.tool_name,
                        "tool_display_name": _tool_display_name(tool_call),
                        "denied_by": getattr(user, "username", None) or actor_label,
                    },
                },
            },
        )
    )

    return tool_call


@transaction.atomic
def revoke_tool_approval_grant(*, grant_id: str, user, run_id: str | None = None) -> ToolApprovalGrant:
    grant = (
        ToolApprovalGrant.objects
        .select_for_update()
        .select_related("run", "workspace")
        .get(id=grant_id)
    )
    if run_id and str(grant.run_id) != str(run_id):
        raise RuntimeError("Approval grant does not belong to this run")
    if grant.revoked_at is not None:
        raise RuntimeError("Approval grant already revoked")
    grant.revoke(user)
    grant.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    append_event(
        run_id=str(grant.run_id),
        event_type=TOOL_APPROVAL_GRANT_REVOKED_EVENT,
        payload={"grant": serialize_grant(grant), "revoked_by": getattr(user, "username", None)},
    )
    _schedule_grants_snapshot_push(run=grant.run)
    return grant


@transaction.atomic
def clear_tool_approval_grants(*, run_id: str, user) -> int:
    run = AgentRun.objects.select_for_update().select_related("workspace").get(id=run_id)
    grants = list(
        ToolApprovalGrant.objects
        .select_for_update()
        .filter(run=run, revoked_at__isnull=True)
    )
    now = timezone.now()
    for grant in grants:
        grant.revoked_at = now
        grant.revoked_by = user
        grant.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    append_event(
        run_id=str(run.id),
        event_type=TOOL_APPROVAL_GRANTS_CLEARED_EVENT,
        payload={"count": len(grants), "cleared_by": getattr(user, "username", None)},
    )
    _schedule_grants_snapshot_push(run=run)
    return len(grants)


def grant_options_for_tool_call(tool_call: ToolCall) -> list[dict[str, str]]:
    return available_grant_options(tool_call.tool_name, tool_call.args or {})
