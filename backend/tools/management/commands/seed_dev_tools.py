from __future__ import annotations

from uuid import UUID

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from agents.models import Agent
from core.models import Workspace
from tools.models import AgentToolGrant, ToolDefinition


class Command(BaseCommand):
    help = "Seed tools, enable workspace definitions, and grant enabled tools to dev agents in one step."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            "-w",
            action="append",
            dest="workspaces",
            help=(
                "Workspace UUID or name. Repeat to target multiple workspaces. "
                "Defaults to Dev Workspace and Finance Workspace."
            ),
        )
        parser.add_argument(
            "--agent",
            action="append",
            dest="agents",
            help="Optional agent name, slug, or UUID. Repeat to target specific agents. Defaults to all agents in the workspace.",
        )
        parser.add_argument(
            "--include-unreleased",
            action="store_true",
            help="Also seed and grant unreleased tools.",
        )

    def handle(self, *args, **options):
        workspace_keys = options.get("workspaces") or ["Dev Workspace", "Finance Workspace"]
        include_unreleased = bool(options.get("include_unreleased"))

        call_command("seed_tools")
        workspace_summaries: list[str] = []
        total_granted_count = 0
        total_enabled_grant_count = 0
        total_updated_agents = 0

        for workspace_key in workspace_keys:
            workspace = self._resolve_workspace(workspace_key)
            workspace_args = ["--workspace", str(workspace.id), "--enable-all"]
            if include_unreleased:
                workspace_args.append("--include-unreleased")
            call_command("seed_workspace_tools", *workspace_args)

            definitions = list(
                ToolDefinition.objects.filter(workspace=workspace, enabled=True, tool__isnull=False)
                .select_related("tool")
                .order_by("tool__name")
            )
            if not definitions:
                raise CommandError(f"No enabled ToolDefinitions found for workspace '{workspace.name}'.")

            agents = self._resolve_agents(workspace, options.get("agents") or [])
            granted_count = 0
            enabled_grant_count = 0
            updated_agents = 0
            if not agents.exists():
                workspace_summaries.append(
                    f"enabled {len(definitions)} workspace definitions for '{workspace.name}', processed 0 agent(s)"
                )
                continue

            for agent in agents.order_by("name"):
                selected = self._current_selected_tools(agent)
                selected_set = set(selected)
                policy_changed = False

                for definition in definitions:
                    tool = definition.tool
                    if tool is None:
                        continue
                    grant, created = AgentToolGrant.objects.get_or_create(
                        agent=agent,
                        tool=tool,
                        defaults={"enabled": True},
                    )
                    if created:
                        granted_count += 1
                    if not grant.enabled:
                        grant.enabled = True
                        grant.save(update_fields=["enabled", "updated_at"])
                        enabled_grant_count += 1
                    if tool.name not in selected_set:
                        selected.append(tool.name)
                        selected_set.add(tool.name)
                        policy_changed = True

                if policy_changed:
                    raw_policy = agent.tool_policy_json if isinstance(agent.tool_policy_json, dict) else {}
                    raw_policy = {**raw_policy, "selected_tools": selected}
                    agent.tool_policy_json = raw_policy
                    agent.save(update_fields=["tool_policy_json", "updated_at"])
                    updated_agents += 1

            total_granted_count += granted_count
            total_enabled_grant_count += enabled_grant_count
            total_updated_agents += updated_agents
            workspace_summaries.append(
                f"enabled {len(definitions)} workspace definitions for '{workspace.name}', "
                f"processed {agents.count()} agent(s), created {granted_count} grants, "
                f"re-enabled {enabled_grant_count} grants, updated selected_tools for {updated_agents} agent(s)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded global tools and workspace definitions for {len(workspace_keys)} workspace(s): "
                + "; ".join(workspace_summaries)
            )
        )

    def _resolve_workspace(self, workspace_key: str) -> Workspace:
        filter_q = Q(name=workspace_key)
        try:
            workspace_uuid = UUID(str(workspace_key))
        except (TypeError, ValueError):
            workspace_uuid = None
        if workspace_uuid:
            filter_q |= Q(id=workspace_uuid)
        workspace = Workspace.objects.filter(filter_q).first()
        if workspace is None:
            raise CommandError(f"Workspace '{workspace_key}' was not found.")
        return workspace

    def _resolve_agents(self, workspace: Workspace, agent_keys: list[str]):
        agents = Agent.objects.filter(Q(workspace=workspace) | Q(workspaces=workspace)).distinct()
        if not agent_keys:
            return agents
        filter_q = Q()
        for key in agent_keys:
            item_q = Q(name=key) | Q(slug=key)
            try:
                agent_uuid = UUID(str(key))
            except (TypeError, ValueError):
                agent_uuid = None
            if agent_uuid:
                item_q |= Q(id=agent_uuid)
            filter_q |= item_q
        return agents.filter(filter_q)

    def _current_selected_tools(self, agent: Agent) -> list[str]:
        raw_policy = agent.tool_policy_json if isinstance(agent.tool_policy_json, dict) else {}
        raw_selected = raw_policy.get("selected_tools") or []
        selected: list[str] = []
        seen: set[str] = set()
        for value in raw_selected:
            tool_name = str(value or "").strip()
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            selected.append(tool_name)
        return selected
