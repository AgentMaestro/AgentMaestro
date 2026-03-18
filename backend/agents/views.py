from decimal import Decimal
from collections import OrderedDict
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from agents.current import agent_creation_context
from agents.utils import (
    build_transport_status,
    find_agent_telegram_endpoint,
    format_provider_display,
    normalize_provider_for_model,
)
from core.models import Workspace, WorkspaceMembership

from .forms import (
    AgentBasicForm,
    AgentLLMForm,
    AgentOwnerForm,
    AgentToolsForm,
    AgentWorkspaceForm,
)
from .models import Agent
from runs.models import AgentRun
from runs.services.handoff import apply_successor_handoff
from runs.services.memory import get_or_create_run_memory
from tools.models import AgentToolGrant, ToolDefinition
from tools.policy import RISK_ORDER, get_effective_tools, visible_tools_for_user
from llm.models import LLMModelProfile
from llm.services.registry import get_client


STEPS = [
    {
        "number": 1,
        "name": "Basics",
        "key": "basics",
        "form": AgentBasicForm,
        "template": "agents/agent_wizard_step1_basics.html",
    },
    {
        "number": 2,
        "name": "LLM",
        "key": "llm",
        "form": AgentLLMForm,
        "template": "agents/agent_wizard_step2_llm.html",
    },
    {
        "number": 3,
        "name": "Workspace",
        "key": "workspace",
        "form": AgentWorkspaceForm,
        "template": "agents/agent_wizard_step3_workspace.html",
    },
    {
        "number": 4,
        "name": "Tools",
        "key": "tools",
        "form": AgentToolsForm,
        "template": "agents/agent_wizard_step4_tools.html",
    },
    {
        "number": 5,
        "name": "Review",
        "key": "review",
        "form": None,
        "template": "agents/agent_wizard_step5_review.html",
    },
]


def _get_step(step_number: int) -> dict[str, object]:
    for step in STEPS:
        if step["number"] == step_number:
            return step
    return STEPS[0]


def _require_sequence(session: dict[str, object], step_number: int) -> int:
    if step_number == 1:
        return 1
    for candidate in range(1, step_number):
        step = _get_step(candidate)
        if step["key"] not in session:
            return candidate
    return step_number


def _wizard_session(request):
    return request.session.setdefault("agent_wizard", {})


def _step_form(step: dict[str, object], request, session_data: dict[str, object]):
    form_class = step["form"]
    if not form_class:
        return None
    initial = _wizard_session(request).get(step["key"], {})
    if request.method == "POST":
        if step["key"] == "tools":
            workspace = _workspace_from_payload(session_data.get("workspace", {}))
            definitions = _workspace_tool_definitions(workspace, request.user)
            return AgentToolsForm(definitions=definitions, data=request.POST)
        return form_class(request.POST)
    if step["key"] == "tools":
        workspace = _workspace_from_payload(session_data.get("workspace", {}))
        definitions = _workspace_tool_definitions(workspace, request.user)
        tools_session = session_data.get("tools") or {}
        if "tool_ids" in tools_session:
            initial_tool_ids = tools_session.get("tool_ids", [])
        else:
            initial_tool_ids = [str(definition.tool_id) for definition in definitions]
        return AgentToolsForm(definitions=definitions, initial_tool_ids=initial_tool_ids)
    if step["key"] == "basics":
        initial = dict(initial)
        initial["sandbox_paths"] = _format_sandbox_initial(initial.get("sandbox_paths"))
    return form_class(initial=initial)


def _save_step_data(request, key: str, data: dict[str, object]) -> None:
    session = _wizard_session(request)
    session[key] = _normalize_for_session(data)
    request.session.modified = True


def _normalize_for_session(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _normalize_for_session(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_for_session(v) for v in value]
    return value


def _format_sandbox_initial(value: object | None) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    if isinstance(value, str):
        return value
    return ""


def _parse_sandbox_paths(raw: object | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        candidates = raw
    else:
        candidates = str(raw).splitlines()
    sanitized: list[str] = []
    for item in candidates:
        candidate = str(item).strip()
        if candidate:
            sanitized.append(candidate)
    return sanitized


def _workspace_from_payload(payload: dict[str, object], create: bool = False) -> Workspace | None:
    workspace_id = payload.get("workspace_id")
    workspace_name = payload.get("workspace_name") or "Default Workspace"
    if workspace_id:
        return Workspace.objects.filter(id=workspace_id).first()
    workspace = Workspace.objects.filter(name=workspace_name).first()
    if workspace:
        return workspace
    if create:
        workspace, _ = Workspace.objects.get_or_create(
            name=workspace_name,
            defaults={"is_active": True},
        )
        return workspace
    return None


def _get_agent_with_access(user, slug: str) -> Agent:
    agent = get_object_or_404(Agent.objects.select_related("workspace"), slug=slug)
    if agent.owner_id == user.id:
        return agent
    if WorkspaceMembership.objects.filter(workspace=agent.workspace, user=user, is_active=True).exists():
        return agent
    raise PermissionDenied


@login_required
@require_http_methods(["GET"])
def agent_run_preallocate(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    predecessor_run = None
    predecessor_run_id = str(request.GET.get("from_run_id") or "").strip()
    rotation_reason = str(request.GET.get("rotation_reason") or "").strip()
    if predecessor_run_id:
        predecessor_run = AgentRun.objects.select_related("agent", "workspace", "started_by").filter(
            id=predecessor_run_id,
            agent=agent,
            workspace=agent.workspace,
        ).first()
    run = AgentRun.objects.create(
        workspace=agent.workspace,
        agent=agent,
        started_by=request.user,
        status=AgentRun.Status.RUNNING,
        channel=AgentRun.Channel.DASHBOARD,
        started_at=timezone.now(),
        input_text="",
    )
    get_or_create_run_memory(run)
    handoff = None
    if predecessor_run is not None:
        handoff = apply_successor_handoff(
            successor_run=run,
            predecessor_run=predecessor_run,
            rotation_reason=rotation_reason,
        )
    return JsonResponse({"run_id": str(run.id), "handoff": handoff})


def _definition_sort_key(definition: ToolDefinition) -> tuple[str, int, str]:
    group_name = definition.tool.tool_group.name if definition.tool.tool_group else ""
    risk_rank = RISK_ORDER.get(definition.tool.risk, 0)
    return (group_name, -risk_rank, definition.tool.name)


def _workspace_tool_definitions(workspace: Workspace | None, user) -> list[ToolDefinition]:
    if not workspace:
        return []
    visible_tools = visible_tools_for_user(user)
    definitions = (
        ToolDefinition.objects.select_related("tool__tool_group")
        .filter(workspace=workspace, enabled=True, tool__isnull=False, tool__in=visible_tools)
    )
    return sorted(definitions, key=_definition_sort_key)


def _build_tool_sections(definitions: list[ToolDefinition], form: AgentToolsForm | None) -> list[dict[str, object]]:
    sections: OrderedDict[str, dict[str, object]] = OrderedDict()
    for definition in definitions:
        group_name = definition.tool.tool_group.name if definition.tool.tool_group else "Ungrouped"
        if group_name not in sections:
            sections[group_name] = {"name": group_name, "tools": []}
        field_name = AgentToolsForm._field_name(definition.tool_id)
        field = form[field_name] if form and field_name in form.fields else None
        sections[group_name]["tools"].append(
            {
                "definition": definition,
                "field": field,
                "selected": bool(field and field.value()),
                "risk_rank": RISK_ORDER.get(definition.tool.risk, 0),
            }
        )
    return list(sections.values())


def _selected_tool_definitions(session_data: dict[str, object], user) -> list[ToolDefinition]:
    workspace_payload = session_data.get("workspace", {})
    workspace = _workspace_from_payload(workspace_payload)
    tool_ids = session_data.get("tools", {}).get("tool_ids", [])
    if not workspace or not tool_ids:
        return []
    visible_tools = visible_tools_for_user(user)
    definitions = (
        ToolDefinition.objects.select_related("tool__tool_group")
        .filter(workspace=workspace, enabled=True, tool__isnull=False, tool__in=visible_tools, tool_id__in=tool_ids)
    )
    return sorted(definitions, key=_definition_sort_key)


def _sidebar_state(*, current_step: int, step_number: int | None = None, enabled: bool = True) -> str:
    if not enabled:
        return "muted"
    if step_number is None:
        return "active" if current_step == len(STEPS) else "pending"
    if current_step == step_number:
        return "active"
    if current_step > step_number:
        return "complete"
    return "pending"


def _build_wizard_sidebar(current_step: int, session_data: dict[str, object]) -> dict[str, object]:
    basics = session_data.get("basics", {})
    llm = session_data.get("llm", {})
    workspace = session_data.get("workspace", {})
    tool_ids = session_data.get("tools", {}).get("tool_ids", [])
    sandbox_paths = basics.get("sandbox_paths") or []
    workspace_name = workspace.get("workspace_name")
    if workspace.get("workspace_id") and not workspace_name:
        workspace_name = "existing workspace"
    if not workspace_name:
        workspace_name = "default workspace"
    selected_tool_count = len(tool_ids)
    tool_value = f"{selected_tool_count} selected" if selected_tool_count else "workspace defaults"
    create_url = reverse("agents:agent_create")
    return {
        "global_actions": [
            {
                "label": "New Agent",
                "href": f"{create_url}?reset=1",
                "target": "_blank",
            },
        ],
        "agent_items": [
            {
                "label": "name",
                "value": basics.get("name") or "not set",
                "href": f"{create_url}?step=1",
                "state": _sidebar_state(current_step=current_step, step_number=1),
            },
            {
                "label": "workspace",
                "value": workspace_name,
                "href": f"{create_url}?step=3",
                "state": _sidebar_state(current_step=current_step, step_number=3),
            },
            {
                "label": "policy",
                "value": llm.get("policy_name") or "react",
                "href": f"{create_url}?step=2",
                "state": _sidebar_state(current_step=current_step, step_number=2),
            },
            {
                "label": "model",
                "value": llm.get("default_model") or Agent.DEFAULT_MODEL,
                "href": f"{create_url}?step=2",
                "state": _sidebar_state(current_step=current_step, step_number=2),
            },
            {
                "label": "allowed paths",
                "value": f"{len(sandbox_paths)} configured" if sandbox_paths else "not set",
                "href": f"{create_url}?step=1",
                "state": _sidebar_state(current_step=current_step, step_number=1),
            },
            {
                "label": "telegram",
                "value": "connect after create",
                "href": None,
                "state": _sidebar_state(current_step=current_step, enabled=False),
            },
            {
                "label": "tools",
                "value": tool_value,
                "href": f"{create_url}?step=4",
                "state": _sidebar_state(current_step=current_step, step_number=4),
            },
        ],
    }


def _build_review_context(session_data: dict[str, object], user) -> dict[str, object]:
    tools = _selected_tool_definitions(session_data, user)
    basics = dict(session_data.get("basics", {}))
    basics.setdefault("sandbox_paths", [])
    name = basics.get("name", "")
    if name:
        unique_name = Agent.generate_unique_name(name)
        basics["unique_name"] = unique_name
        basics["name_conflict"] = unique_name != name
    return {
        "basics": basics,
        "llm": session_data.get("llm", {}),
        "tools": tools,
        "workspace": session_data.get("workspace", {}),
    }


@login_required
@require_http_methods(["GET", "POST"])
def agent_create_wizard(request):
    if request.GET.get("reset"):
        request.session["agent_wizard"] = {}
        return redirect(reverse("agents:agent_create"))

    step = int(request.GET.get("step", 1))
    step = max(1, min(step, len(STEPS)))
    session_data = _wizard_session(request)
    step = _require_sequence(session_data, step)
    step_def = _get_step(step)

    form = _step_form(step_def, request, session_data)
    owner_form = None
    if step == STEPS[-1]["number"] and (request.user.is_staff or request.user.is_superuser):
        owner_form = AgentOwnerForm(
            data=request.POST if request.method == "POST" else None,
            users=get_user_model().objects.filter(is_active=True),
        )

    if request.method == "POST":
        if step_def["form"] and form and form.is_valid():
            if step_def["key"] == "tools" and isinstance(form, AgentToolsForm):
                cleaned = {"tool_ids": form.get_selected_tool_ids()}
            else:
                cleaned = form.cleaned_data
            if step_def["key"] == "workspace":
                workspace = cleaned.get("workspace")
                workspace_name = cleaned.get("workspace_name") or ""
                cleaned = {
                    "workspace_id": str(workspace.id) if workspace else None,
                    "workspace_name": workspace_name,
                }
            if step_def["key"] == "basics":
                cleaned["sandbox_paths"] = _parse_sandbox_paths(cleaned.get("sandbox_paths"))
            _save_step_data(request, step_def["key"], cleaned)
            if step == len(STEPS):
                return _complete_wizard(request, session_data, owner_form)
            next_step = step + 1
            return redirect(f"{reverse('agents:agent_create')}?step={next_step}")
        elif step == len(STEPS):
            return _complete_wizard(request, session_data, owner_form)

    tool_sections: list[dict[str, object]] = []
    if isinstance(form, AgentToolsForm):
        tool_sections = _build_tool_sections(form.definitions, form)

    context = {
        "steps": STEPS,
        "current_step": step,
        "form": form,
        "owner_form": owner_form,
        "review": _build_review_context(session_data, request.user),
        "tool_sections": tool_sections,
        "wizard_sidebar": _build_wizard_sidebar(step, session_data),
        "wizard_reset_url": f"{reverse('agents:agent_create')}?reset=1",
    }
    return render(request, step_def["template"], context)


def _complete_wizard(request, session_data: dict[str, object], owner_form: forms.Form | None):
    review_data = _build_review_context(session_data, request.user)
    workspace_payload = session_data.get("workspace", {})
    basics = review_data["basics"]
    llm = review_data["llm"]
    tools = review_data["tools"]

    owner = None
    if owner_form and owner_form.is_valid():
        owner = owner_form.cleaned_data.get("owner")
    elif owner_form and not owner_form.is_valid():
        context = {
            "steps": STEPS,
            "current_step": len(STEPS),
            "form": None,
            "owner_form": owner_form,
            "review": review_data,
            "wizard_sidebar": _build_wizard_sidebar(len(STEPS), session_data),
            "wizard_reset_url": f"{reverse('agents:agent_create')}?reset=1",
        }
        return render(request, STEPS[-1]["template"], context)

    workspace = _workspace_from_payload(workspace_payload, create=True)
    with agent_creation_context(request.user):
        agent = Agent.objects.create(
            workspace=workspace,
            name=basics.get("name", "New Agent"),
            description=basics.get("description", "") or "",
            soul=basics.get("soul", ""),
            default_model=llm.get("default_model", "gpt-5"),
            temperature=llm.get("temperature", "0.70"),
            policy_name=llm.get("policy_name", "react"),
            role=llm.get("role", "assisting"),
            tool_policy_json={"selected_tools": [definition.tool.name for definition in tools]},
            sandbox_paths=basics.get("sandbox_paths", []),
            owner=owner or request.user,
        )
    _create_tool_grants(agent, workspace, session_data)

    request.session.pop("agent_wizard", None)
    messages.success(request, f"Agent '{agent.name}' created successfully.")
    return redirect(f"/agents/{agent.slug}/")


def _create_tool_grants(agent: Agent, workspace: Workspace, session_data: dict[str, object]) -> None:
    tool_ids = session_data.get("tools", {}).get("tool_ids", [])
    if not tool_ids:
        return
    definitions = ToolDefinition.objects.filter(workspace=workspace, tool_id__in=tool_ids, enabled=True)
    grants = [
        AgentToolGrant(agent=agent, tool=definition.tool, enabled=True)
        for definition in definitions
        if definition.tool
    ]
    AgentToolGrant.objects.bulk_create(grants)


def _resolve_transport_for_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower() or "openai"
    if normalized == "openai":
        return (getattr(settings, "OPENAI_TRANSPORT", "ws") or "ws").lower()
    if normalized == "gemini":
        return (getattr(settings, "GEMINI_TRANSPORT", "http") or "http").lower()
    try:
        client = get_client(normalized)
        resolver = getattr(client, "resolve_transport", None)
        if callable(resolver):
            return str(resolver() or "http").lower()
        return str(getattr(client, "transport", "http") or "http").lower()
    except Exception:
        return "http"


@login_required
def agent_detail(request, slug: str):
    agent = get_object_or_404(Agent.objects.select_related("workspace", "owner"), slug=slug)
    if not _can_access_agent(request.user, agent):
        raise PermissionDenied("Workspace access required to view this agent.")

    owner_agents = []
    if request.user.is_active and request.user.is_authenticated and request.user.id == agent.owner_id:
        owner_agents = (
            Agent.objects.filter(owner=request.user)
            .order_by("name")
            .values("name", "slug")
        )

    profile = (
        LLMModelProfile.objects.filter(name=agent.policy_name, is_active=True)
        .order_by("name")
        .first()
    )
    provider = profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
    model_name = profile.model if profile else agent.default_model
    normalized_provider = normalize_provider_for_model(provider, model_name)
    provider_label = format_provider_display(normalized_provider)
    transport = _resolve_transport_for_provider(normalized_provider)
    transport_label = "WS" if transport.lower() == "ws" else "HTTP"

    effective_tools = get_effective_tools(agent, request.user)
    telegram_endpoint = find_agent_telegram_endpoint(agent)
    context = {
        "agent": agent,
        "workspace": agent.workspace,
        "tool_count": len(effective_tools),
        "tools": _serialize_effective_tools(effective_tools),
        "owner_agents": list(owner_agents),
        "websocket_url": f"/ws/agents/{agent.slug}/chat/",
        "sandbox_paths": agent.sandbox_paths or [],
        "telegram_status": build_transport_status(agent, telegram_endpoint),
        "user_name": (request.user.get_full_name() or request.user.username or "You").strip() or "You",
        "llm_provider": normalized_provider,
        "llm_provider_label": provider_label,
        "llm_model": model_name,
        "llm_transport": transport,
        "llm_transport_label": transport_label,
    }
    return render(request, "agents/agent_detail.html", context)


def _serialize_effective_tools(effective_tools: list) -> list[dict[str, object]]:
    tools: list[dict[str, object]] = []
    for entry in effective_tools:
        group_name = ""
        if entry.definition and entry.definition.tool and entry.definition.tool.tool_group:
            group_name = entry.definition.tool.tool_group.name
        tools.append(
            {
                "name": entry.tool.name,
                "description": entry.description,
                "risk": entry.risk,
                "requires_approval": entry.requires_approval,
                "group": group_name,
            }
        )
    return tools


def _can_access_agent(user, agent: Agent) -> bool:
    if user.id == agent.owner_id:
        return True
    return WorkspaceMembership.objects.filter(
        workspace=agent.workspace, user=user, is_active=True
    ).exists()
