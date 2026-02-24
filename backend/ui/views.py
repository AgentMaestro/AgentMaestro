import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from core.models import Workspace
from runs.models import AgentRun
from runs.tasks import run_tick as run_tick_task

DEFAULT_DEV_WORKSPACE = "Dev Workspace"
DEFAULT_DEV_AGENT = "Dev Agent"


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


@require_http_methods(["GET", "POST"])
def dev_start_run(request):
    if request.method == "GET":
        return render(request, "ui/dev_start_run.html")

    payload = {}
    if request.content_type == "application/json" and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
    input_text = payload.get("input_text", "Hello from Dev Run")

    workspace = _get_or_create_dev_workspace()
    agent = _get_or_create_dev_agent(workspace)
    run = AgentRun.objects.create(
        workspace=workspace,
        agent=agent,
        input_text=input_text,
        status=AgentRun.Status.PENDING,
        channel=AgentRun.Channel.DASHBOARD,
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
    return render(request, "ui/run_detail.html", {"run": run})
