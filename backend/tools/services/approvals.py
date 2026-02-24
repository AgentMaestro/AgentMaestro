# backend/tools/services/approvals.py
from __future__ import annotations

from typing import Any, Dict, Optional

from django.db import transaction
from runs.models import AgentRun, AgentStep
from runs.services.events import (
    append_event,
    broadcast_approvals_event,
)
from runs.services.state import transition_run
from runs.services.steps import append_step
from tools.models import ToolCall


TOOL_CALL_REQUESTED_EVENT = "tool_call_requested"
TOOL_CALL_APPROVED_EVENT = "tool_call_approved"


def _schedule_approvals_push(*, workspace_id: str, event: str, data: Dict[str, Any]) -> None:
    def _do_broadcast():
        broadcast_approvals_event(workspace_id=workspace_id, event=event, data=data)

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

    step = append_step(
        run_id=run_id,
        kind=AgentStep.Kind.TOOL_CALL,
        payload={"tool_name": tool_name, "args": args or {}},
    )

    tool_call = ToolCall.objects.create(
        run=run,
        step=step,
        tool_name=tool_name,
        args=args or {},
        requires_approval=requires_approval,
        status=ToolCall.Status.PENDING if requires_approval else ToolCall.Status.APPROVED,
    )

    append_event(
        run_id=run_id,
        event_type=TOOL_CALL_REQUESTED_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "args": tool_call.args,
            "step_index": step.step_index,
        },
    )

    if requires_approval:
        transition_run(run_id=run_id, new_status=AgentRun.Status.WAITING_FOR_APPROVAL)
        _schedule_approvals_push(
            workspace_id=str(run.workspace_id),
            event=TOOL_CALL_REQUESTED_EVENT,
            data={
                "run_id": str(run.id),
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_call.tool_name,
                "status": ToolCall.Status.PENDING,
            },
        )

    return tool_call


@transaction.atomic
def approve_tool_call(*, tool_call_id: str, user) -> ToolCall:
    tool_call = (
        ToolCall.objects
        .select_for_update()
        .select_related("run", "run__workspace")
        .get(id=tool_call_id)
    )

    if not tool_call.requires_approval:
        raise RuntimeError("Tool call does not require approval")
    if tool_call.status != ToolCall.Status.PENDING:
        raise RuntimeError("Tool call already acted on")

    tool_call.mark_approved(user)
    tool_call.status = ToolCall.Status.APPROVED
    tool_call.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

    append_event(
        run_id=str(tool_call.run_id),
        event_type=TOOL_CALL_APPROVED_EVENT,
        payload={
            "tool_call_id": str(tool_call.id),
            "approved_by": getattr(user, "username", None),
        },
    )

    transition_run(run_id=str(tool_call.run_id), new_status=AgentRun.Status.RUNNING)

    _schedule_approvals_push(
        workspace_id=str(tool_call.run.workspace_id),
        event=TOOL_CALL_APPROVED_EVENT,
        data={
            "run_id": str(tool_call.run_id),
            "tool_call_id": str(tool_call.id),
            "approved_by": getattr(user, "username", None),
            "status": ToolCall.Status.APPROVED,
        },
    )

    return tool_call
