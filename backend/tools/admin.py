from copy import deepcopy

from django.contrib import admin, messages
from django.utils.html import format_html
from django.urls import reverse

from .models import Tool, ToolCall, ToolDefinition, ToolGroup, AgentToolGrant


@admin.register(ToolGroup)
class ToolGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ("tool_group", "name", "description", "risk", "requires_approval", "released", "updated_at")
    list_filter = ("tool_group", "risk", "requires_approval", "released")
    search_fields = ("name", "slug", "tool_group__name")


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("tool", "description", "workspace", "default_risk_level", "enabled", "default_requires_approval")
    list_filter = ("workspace", "default_risk_level", "enabled")
    search_fields = ("tool__name", "workspace__name")
    actions = ("sync_to_tools",)

    @admin.action(description="Sync to Tools")
    def sync_to_tools(self, request, queryset):
        synced = 0
        missing_names: list[str] = []

        for definition in queryset.select_related("tool"):
            tool_name = (definition.tool.name if definition.tool else definition.name).strip()
            if not tool_name:
                missing_names.append(f"{definition.workspace}:<missing name>")
                continue

            tool = Tool.objects.filter(name=tool_name).first()
            if not tool:
                missing_names.append(tool_name)
                continue

            definition.description = tool.description
            definition.args_schema = deepcopy(tool.args_schema or {})
            definition.save(update_fields=["description", "args_schema", "updated_at"])
            synced += 1

        if synced:
            self.message_user(
                request,
                f"Synced {synced} ToolDefinition entr{'y' if synced == 1 else 'ies'} from Tool records.",
                level=messages.SUCCESS,
            )
        if missing_names:
            preview = ", ".join(missing_names[:5])
            more = "" if len(missing_names) <= 5 else f" (+{len(missing_names) - 5} more)"
            self.message_user(
                request,
                f"No matching Tool found for: {preview}{more}",
                level=messages.WARNING,
            )


@admin.register(AgentToolGrant)
class AgentToolGrantAdmin(admin.ModelAdmin):
    list_display = ("agent", "tool", "enabled", "created_at")
    list_filter = ("enabled", "tool")
    search_fields = ("agent__name", "tool__name")


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "tool_name", "status", "requires_approval", "run_link")
    list_filter = ("status", "requires_approval")
    search_fields = ("id", "tool_name", "run__id")

    def run_link(self, obj: ToolCall) -> str:
        url = reverse("ui:run_detail", kwargs={"run_id": obj.run_id})
        return format_html('<a href="{}">{}</a>', url, obj.run_id)
    run_link.short_description = "Run"
