from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from core.models import WorkspaceMembership
from core.services.limits import LimitExceeded, LimitKey, LimitType, QUOTA_MANAGER
from runs.models import AgentRun
from runs.services.snapshot import get_run_snapshot
from runs.services.subruns import spawn_subrun
from runs.tasks import run_tick as run_tick_task
from tools.models import ToolCall
from tools.services.approvals import approve_tool_call as approve_tool_call_service

APPROVAL_ROLES = {
    WorkspaceMembership.Role.OWNER,
    WorkspaceMembership.Role.ADMIN,
    WorkspaceMembership.Role.OPERATOR,
}


def _json_payload(request):
    if not request.body:
        return {}
    content_type = request.content_type or ""
    if "application/json" not in content_type:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _require_membership(request, workspace_id: str) -> WorkspaceMembership:
    user = request.user
    if not user or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication required")
    membership = WorkspaceMembership.objects.filter(
        workspace_id=workspace_id,
        user=user,
        is_active=True,
    ).first()
    if not membership:
        raise PermissionDenied("Workspace membership required")
    return membership


def _ensure_role(membership: WorkspaceMembership, allowed_roles: set[str]) -> None:
    if membership.role not in allowed_roles:
        raise PermissionDenied("Insufficient role for this operation")


def _enforce_workspace_quota(workspace_id: str, limit_key: str) -> Optional[JsonResponse]:
    try:
        QUOTA_MANAGER.record_request(str(workspace_id), limit_key)
    except LimitExceeded as exc:
        return _json_error(
            f"Workspace quota exceeded for {exc.limit.name}. "
            f"Allowed {exc.limit.max_requests} req/sec. Try again later.",
            status=429,
        )
    return None


def _limit_message(exc: LimitExceeded) -> str:
    if exc.limit.limit_type == LimitType.RATE:
        return f"Allowed {exc.limit.max_requests} req/sec."
    return f"Allowed {exc.limit.max_concurrency} concurrent slots."


@csrf_exempt
@require_http_methods(["POST"])
def start_run(request):
    try:
        payload = _json_payload(request)
    except ValueError as exc:
        return _json_error(str(exc), status=400)

    workspace_id = payload.get("workspace_id")
    agent_id = payload.get("agent_id")
    if not workspace_id or not agent_id:
        return _json_error("workspace_id and agent_id are required", status=400)

    try:
        _require_membership(request, workspace_id)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403)

    quota_error = _enforce_workspace_quota(workspace_id, LimitKey.RUN_CREATION)
    if quota_error:
        return quota_error

    agent = get_object_or_404(Agent, id=agent_id, workspace_id=workspace_id)
    channel = payload.get("channel", AgentRun.Channel.DASHBOARD)
    if channel not in AgentRun.Channel.values:
        channel = AgentRun.Channel.DASHBOARD

    try:
        with transaction.atomic():
            run_id = uuid.uuid4()
            run = AgentRun.objects.create(
                id=run_id,
                workspace_id=workspace_id,
                agent=agent,
                input_text=payload.get("input_text", ""),
                status=AgentRun.Status.PENDING,
                channel=channel,
                started_by=request.user,
            )
            QUOTA_MANAGER.acquire_run_slots(workspace_id, str(run.id), include_parent=True)
    except LimitExceeded as exc:
        return _json_error(
            f"Workspace concurrency limit ({exc.limit.name}) exceeded. {_limit_message(exc)}",
            status=429,
        )

    run_tick_task.delay(str(run.id))

    return JsonResponse(
        {
            "run_id": str(run.id),
            "status": run.status,
            "workspace_id": str(workspace_id),
            "agent_id": str(agent.id),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def spawn_subrun_view(request, run_id: str):
    run = get_object_or_404(AgentRun, id=run_id)
    try:
        _require_membership(request, run.workspace_id)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403)

    try:
        payload = _json_payload(request)
    except ValueError as exc:
        return _json_error(str(exc), status=400)

    spawn_kwargs: Dict[str, Any] = {"parent_run_id": run_id}
    if "input_text" in payload:
        spawn_kwargs["input_text"] = payload.get("input_text")

    options = payload.get("options") or {}
    for key in {"join_policy", "quorum", "timeout_seconds", "failure_policy", "group_id", "metadata"}:
        if key in options:
            spawn_kwargs[key] = options[key]

    try:
        child = spawn_subrun(**spawn_kwargs)
    except Exception as exc:
        if isinstance(exc, LimitExceeded):
            return _json_error(
                f"Workspace quota exceeded for {exc.limit.name}. {_limit_message(exc)} Try again later.",
                status=429,
            )
        return _json_error(str(exc), status=400)

    return JsonResponse(
        {
            "child_run_id": str(child.id),
            "status": child.status,
            "correlation_id": str(child.correlation_id),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def approve_tool_call_view(request, tool_call_id: str):
    tool_call = get_object_or_404(ToolCall.objects.select_related("run__workspace"), id=tool_call_id)
    try:
        membership = _require_membership(request, tool_call.run.workspace_id)
        _ensure_role(membership, APPROVAL_ROLES)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403)

    try:
        approved = approve_tool_call_service(tool_call_id=str(tool_call.id), user=request.user)
    except Exception as exc:
        return _json_error(str(exc), status=400)

    run_tick_task.delay(str(approved.run_id))
    return JsonResponse(
        {
            "tool_call_id": str(approved.id),
            "run_id": str(approved.run_id),
            "status": approved.status,
        }
    )


@require_http_methods(["GET"])
def run_snapshot_view(request, run_id: str):
    run = get_object_or_404(AgentRun, id=run_id)
    try:
        _require_membership(request, run.workspace_id)
    except PermissionDenied as exc:
        return _json_error(str(exc), status=403)

    quota_error = _enforce_workspace_quota(run.workspace_id, LimitKey.SNAPSHOT)
    if quota_error:
        return quota_error

    since_seq = request.GET.get("since_seq")
    parsed_seq: Optional[int] = None
    if since_seq is not None and since_seq != "":
        try:
            parsed_seq = int(since_seq)
        except ValueError:
            return _json_error("since_seq must be an integer", status=400)

    snapshot = get_run_snapshot(run_id=run_id, since_seq=parsed_seq)
    return JsonResponse(snapshot)
