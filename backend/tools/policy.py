from dataclasses import dataclass
from typing import Iterable

from django.db.models import Case, IntegerField, Value, When

from .models import AgentToolGrant, Tool, ToolDefinition, ToolRisk

RISK_ORDER = {
    ToolRisk.SAFE: 0,
    ToolRisk.ELEVATED: 1,
    ToolRisk.DANGEROUS: 2,
}

def _max_risk(*values: Iterable[str]) -> str:
    return max(values, key=lambda value: RISK_ORDER.get(value, 0))


@dataclass(frozen=True)
class EffectiveTool:
    tool: Tool
    definition: ToolDefinition
    requires_approval: bool
    risk: str
    args_schema: dict
    description: str


class ToolNotAllowedError(Exception):
    pass


def _required_parameters(tool: Tool, args_schema: dict) -> list[str]:
    configured = list(tool.required_parameters or [])
    if configured:
        return configured
    return list((args_schema or {}).get("required") or [])


def _format_tool_description(tool: Tool, definition: ToolDefinition, args_schema: dict) -> str:
    description = (definition.description or tool.description or "").strip()
    if tool.name in {"remember", "search_memory"}:
        memory_note = (
            "Use the actual tool directly; do not emit code-like placeholders such as "
            "`default_api.remember(...)`, `print(default_api.remember(...))`, or `tool_code` text when the memory tool is available."
        )
        if memory_note not in description:
            description = f"{description}\n\n{memory_note}".strip() if description else memory_note
    required = _required_parameters(tool, args_schema)
    if not required:
        return description
    required_text = "REQUIRED PARAMETERS: " + ", ".join(required)
    if required_text in description:
        return description
    if not description:
        return required_text
    return f"{description}\n\n{required_text}"


def visible_tools_for_user(user):
    if user and user.is_superuser:
        return Tool.objects.all()
    return Tool.objects.filter(released=True)


def get_effective_tools(agent, user):
    if not agent:
        return []

    workspace_ids = list(getattr(agent, "get_accessible_workspace_ids", lambda: [])() or [])
    if not workspace_ids and getattr(agent, "workspace_id", None):
        workspace_ids = [agent.workspace_id]
    if not workspace_ids:
        return []

    workspace_order = Case(
        *[
            When(workspace_id=workspace_id, then=Value(index))
            for index, workspace_id in enumerate(workspace_ids)
        ],
        default=Value(len(workspace_ids)),
        output_field=IntegerField(),
    )

    definitions = (
        ToolDefinition.objects.select_related("tool", "workspace")
        .annotate(_workspace_order=workspace_order)
        .filter(workspace_id__in=workspace_ids, enabled=True, tool__isnull=False)
        .order_by("_workspace_order", "tool__name")
    )
    grants = {
        grant.tool_id: grant
        for grant in AgentToolGrant.objects.filter(agent=agent, enabled=True)
    }
    effective_tools = []
    seen_tool_ids: set = set()
    for definition in definitions:
        tool = definition.tool
        if not tool:
            continue
        if not tool.released and not (user and user.is_superuser):
            continue
        if grants.get(tool.id) is None:
            continue
        if tool.id in seen_tool_ids:
            continue
        risk = _max_risk(tool.risk, definition.default_risk_level)
        requires_approval = tool.requires_approval or definition.default_requires_approval
        args_schema = definition.args_schema or tool.args_schema or {}
        description = _format_tool_description(tool, definition, args_schema)
        effective_tools.append(
            EffectiveTool(
                tool=tool,
                definition=definition,
                requires_approval=requires_approval,
                risk=risk,
                args_schema=args_schema,
                description=description,
            )
        )
        seen_tool_ids.add(tool.id)
    return effective_tools


def assert_tool_allowed(agent, user, tool_identifier):
    effective = get_effective_tools(agent, user)
    for entry in effective:
        if entry.tool.name == tool_identifier or entry.tool.slug == tool_identifier:
            return entry
    raise ToolNotAllowedError(f"Tool '{tool_identifier}' is not allowed for agent {agent}.")
