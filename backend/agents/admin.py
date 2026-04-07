from copy import deepcopy

from django import forms
from django.contrib import admin, messages
from django.utils.safestring import mark_safe

from .models import Agent
from tools.models import AgentToolGrant, Tool, ToolDefinition


class AgentAdminForm(forms.ModelForm):
    default_model = forms.ChoiceField(label="Default Model")
    reasoning = forms.ChoiceField(label="Reasoning")

    class Meta:
        model = Agent
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Agent.get_default_model_choices()
        self.fields["default_model"].choices = choices
        self.fields["reasoning"].choices = Agent.get_reasoning_choices()


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "workspace",
        "default_model",
        "reasoning",
        "formatted_sandbox_paths",
        "soul",
        "owner",
    )
    search_fields = ("id", "name", "slug")
    list_filter = ("workspace", "owner")
    filter_horizontal = ("workspaces",)
    form = AgentAdminForm
    readonly_fields = ("backup_models_guide",)
    fieldsets = (
        (
            "Agent",
            {
                "fields": (
                    "name",
                    "slug",
                    "workspace",
                    "workspaces",
                    "owner",
                    "description",
                    "soul",
                    "default_model",
                    "reasoning",
                    "temperature",
                    "policy_name",
                    "tool_policy_json",
                    "sandbox_paths",
                    "backup_models_json",
                    "backup_retry_policy_json",
                    "backup_models_guide",
                    "created_by",
                    "default_conversation",
                )
            },
        ),
    )

    def formatted_sandbox_paths(self, obj: Agent) -> str:
        paths = Agent._normalize_sandbox_paths(getattr(obj, "sandbox_paths", None))
        return ", ".join(paths)
    formatted_sandbox_paths.short_description = "Sandbox"

    def backup_models_guide(self, obj: Agent) -> str:
        return mark_safe(
            """
            <div style="padding: 12px 14px; border: 1px solid #3a3f46; border-radius: 6px; background: #1f2329;">
              <strong>Backup model format</strong>
              <div style="margin-top: 8px;">
                Use an ordered JSON list. Each entry should resolve to a row in <code>ModelsAvailable</code>.
              </div>
              <pre style="margin-top: 10px; white-space: pre-wrap; background: #11151a; color: #d7dce2; padding: 10px; border-radius: 4px;">[
  {"company": "google", "api": "gemini", "name": "gemini-2.5-flash"},
  {"company": "openai", "api": "responses", "name": "gpt-5-mini"}
]</pre>
              <div style="margin-top: 8px;">
                The retry policy is a JSON object. MVP defaults:
                <code>{"retry_same_model_attempts": 1, "retryable_status_codes": [429, 502, 503, 504]}</code>
              </div>
            </div>
            """
        )
    backup_models_guide.short_description = "Backup Model Help"

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
