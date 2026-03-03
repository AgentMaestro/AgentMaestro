from dataclasses import dataclass
from typing import Iterable


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


def visible_tools_for_user(user):
    if user and user.is_superuser:
        return Tool.objects.all()
    return Tool.objects.filter(released=True)


def get_effective_tools(agent, user):
    if not agent:
        return []

    definitions = (
        ToolDefinition.objects.select_related("tool")
        .filter(workspace=agent.workspace, enabled=True, tool__isnull=False)
    )
    grants = {
        grant.tool_id: grant
        for grant in AgentToolGrant.objects.filter(agent=agent, enabled=True)
    }
    effective_tools = []
    for definition in definitions:
        tool = definition.tool
        if not tool:
            continue
        if not tool.released and not (user and user.is_superuser):
            continue
        if grants.get(tool.id) is None:
            continue
        risk = _max_risk(tool.risk, definition.default_risk_level)
        requires_approval = tool.requires_approval or definition.default_requires_approval
        args_schema = definition.args_schema or tool.args_schema or {}
        description = definition.description or tool.description
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
    return effective_tools


def assert_tool_allowed(agent, user, tool_identifier):
    effective = get_effective_tools(agent, user)
    for entry in effective:
        if entry.tool.name == tool_identifier or entry.tool.slug == tool_identifier:
            return entry
    raise ToolNotAllowedError(f"Tool '{tool_identifier}' is not allowed for agent {agent}.")
