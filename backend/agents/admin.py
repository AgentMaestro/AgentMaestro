from copy import deepcopy

from django import forms
from django.contrib import admin, messages

from .models import Agent
from tools.models import AgentToolGrant, Tool, ToolDefinition


class AgentAdminForm(forms.ModelForm):
    default_model = forms.ChoiceField(label="Default Model")

    class Meta:
        model = Agent
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Agent.get_default_model_choices()
        self.fields["default_model"].choices = choices


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "workspace",
        "role",
        "default_model",
        "formatted_sandbox_paths",
        "soul",
        "owner",
    )
    search_fields = ("id", "name", "slug")
    list_filter = ("workspace", "owner")
    form = AgentAdminForm

    def formatted_sandbox_paths(self, obj: Agent) -> str:
        paths = Agent._normalize_sandbox_paths(getattr(obj, "sandbox_paths", None))
        return ", ".join(paths)
    formatted_sandbox_paths.short_description = "Sandbox"

    def save_model(self, request, obj: Agent, form, change):
        super().save_model(request, obj, form, change)
        self._sync_selected_tools(request, obj)

    def _sync_selected_tools(self, request, agent: Agent) -> None:
        raw_policy = agent.tool_policy_json if isinstance(agent.tool_policy_json, dict) else {}
        raw_selected = raw_policy.get("selected_tools") or []
        selected_tools: list[str] = []
        seen: set[str] = set()
        for value in raw_selected:
            tool_name = str(value or "").strip()
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            selected_tools.append(tool_name)
        if not selected_tools:
            return

        missing_tools: list[str] = []
        synced_tools: list[str] = []

        for tool_name in selected_tools:
            tool = Tool.objects.filter(name=tool_name).first()
            if tool is None:
                missing_tools.append(tool_name)
                continue

            definition = ToolDefinition.objects.filter(workspace=agent.workspace, tool=tool).first()
            if definition is None:
                definition = (
                    ToolDefinition.objects
                    .filter(workspace=agent.workspace, tool__isnull=True, name=tool_name)
                    .first()
                )

            if definition is None:
                ToolDefinition.objects.create(
                    workspace=agent.workspace,
                    tool=tool,
                    name=tool.name,
                    description=tool.description,
                    args_schema=deepcopy(tool.args_schema or {}),
                    default_risk_level=tool.risk,
                    default_requires_approval=tool.requires_approval,
                    enabled=True,
                    config={},
                )
            else:
                updated_fields: list[str] = []
                if definition.tool_id != tool.id:
                    definition.tool = tool
                    updated_fields.append("tool")
                if definition.name != tool.name:
                    definition.name = tool.name
                    updated_fields.append("name")
                if not definition.enabled:
                    definition.enabled = True
                    updated_fields.append("enabled")
                if updated_fields:
                    definition.save(update_fields=[*updated_fields, "updated_at"])

            grant, created = AgentToolGrant.objects.get_or_create(
                agent=agent,
                tool=tool,
                defaults={"enabled": True},
            )
            if not created and not grant.enabled:
                grant.enabled = True
                grant.save(update_fields=["enabled", "updated_at"])
            synced_tools.append(tool_name)

        if synced_tools:
            preview = ", ".join(synced_tools[:5])
            more = "" if len(synced_tools) <= 5 else f" (+{len(synced_tools) - 5} more)"
            self.message_user(
                request,
                f"Synced tool access for: {preview}{more}.",
                level=messages.SUCCESS,
            )
        if missing_tools:
            preview = ", ".join(missing_tools[:5])
            more = "" if len(missing_tools) <= 5 else f" (+{len(missing_tools) - 5} more)"
            self.message_user(
                request,
                f"No matching Tool catalog entries found for: {preview}{more}",
                level=messages.WARNING,
            )
