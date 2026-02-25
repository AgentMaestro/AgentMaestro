import json
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model, login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from core.services.limits import LimitExceeded, LimitKey, QUOTA_MANAGER
from runs.models import AgentRun, RunArchive
from runs.services.snapshot import get_run_snapshot
from runs.tasks import run_tick as run_tick_task

DEFAULT_DEV_WORKSPACE = "Dev Workspace"
DEFAULT_DEV_AGENT = "Dev Agent"
DEFAULT_DEV_OPERATOR = "dev-operator"


def dev_ws_test(request):
    return render(request, "ui/dev_ws_test.html")


def _get_or_create_dev_workspace() -> Workspace:
    workspace, _ = Workspace.objects.get_or_create(name=DEFAULT_DEV_WORKSPACE, defaults={"is_active": True})
    return workspace


def _get_or_create_dev_agent(workspace: Workspace) -> Agent:
    agent_qs = Agent.objects.filter(workspace=workspace, name=DEFAULT_DEV_AGENT)
    if agent_qs.exists():
        return agent_qs.first()
    return Agent.objects.create(
        workspace=workspace,
        name=DEFAULT_DEV_AGENT,
        system_prompt="You are the Dev Runner. Follow instructions carefully.",
    )


def _get_or_create_dev_user():
    User = get_user_model()
    user, created = User.objects.get_or_create(username=DEFAULT_DEV_OPERATOR, defaults={"is_active": True})
    if created or not user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def _ensure_workspace_membership(workspace: Workspace, user):
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OWNER},
    )


def _ensure_dev_user_session(request):
    user = _get_or_create_dev_user()
    login(request, user)
    return user


def _assert_workspace_access(user, workspace_id: str):
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required")
    if not WorkspaceMembership.objects.filter(
        workspace_id=workspace_id,
        user=user,
        is_active=True,
    ).exists():
        raise PermissionDenied("Workspace membership required")



@require_http_methods(["GET", "POST"])
def dev_start_run(request):
    user = _ensure_dev_user_session(request)
    workspace = _get_or_create_dev_workspace()
    _ensure_workspace_membership(workspace, user)

    if request.method == "GET":
        return render(request, "ui/dev_start_run.html")

    try:
        QUOTA_MANAGER.record_request(str(workspace.id), LimitKey.RUN_CREATION)
    except LimitExceeded:
        return JsonResponse(
            {"error": "Workspace rate limit exceeded for starting runs. Try again in a moment."},
            status=429,
        )

    payload = {}
    if request.content_type == "application/json" and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
    input_text = payload.get("input_text", "Hello from Dev Run")

    agent = _get_or_create_dev_agent(workspace)
    try:
        with transaction.atomic():
            run_id = uuid.uuid4()
            run = AgentRun.objects.create(
                id=run_id,
                workspace=workspace,
                agent=agent,
                input_text=input_text,
                status=AgentRun.Status.PENDING,
                channel=AgentRun.Channel.DASHBOARD,
                started_by=user,
            )
            QUOTA_MANAGER.acquire_run_slots(str(workspace.id), str(run.id), include_parent=True)
    except LimitExceeded:
        return JsonResponse(
            {"error": "Workspace concurrency limit for new runs reached. Try again shortly."},
            status=429,
        )

    run_tick_task.delay(str(run.id))

    return JsonResponse(
        {
            "run_id": str(run.id),
            "run_url": reverse("ui:run_detail", kwargs={"run_id": run.id}),
        }
    )


def run_detail(request, run_id: str):
    run = get_object_or_404(AgentRun.objects.select_related("workspace", "agent"), id=run_id)
    return render(
        request,
        "ui/run_detail.html",
        {
            "run": run,
            "run_archives": run.archives.order_by("-created_at"),
        },
    )


def run_snapshot(request, run_id: str):
    run = get_object_or_404(AgentRun, id=run_id)
    _assert_workspace_access(request.user, run.workspace_id)
    snapshot = get_run_snapshot(run_id=run_id)
    return JsonResponse(snapshot)


def download_run_archive(request, run_id: str, archive_id: str):
    run = get_object_or_404(AgentRun.objects.select_related("workspace"), id=run_id)
    _assert_workspace_access(request.user, run.workspace_id)
    archive = get_object_or_404(RunArchive, id=archive_id, run=run)
    archive_path = Path(archive.archive_path)
    if not archive_path.exists():
        raise Http404("Archive bundle not available")
    return FileResponse(
        archive_path.open("rb"),
        as_attachment=True,
        filename=archive_path.name,
    )
