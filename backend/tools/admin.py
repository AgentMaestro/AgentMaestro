import json
from copy import deepcopy

from django.contrib import admin, messages
from django.core.management import call_command
from django.http import HttpResponse
from django.utils.html import format_html
from django.urls import reverse

from core.admin_utils import format_datetime_eastern

from .models import Tool, ToolApprovalGrant, ToolCall, ToolDefinition, ToolGroup, AgentToolGrant


def _serialize_admin_value(value):
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if hasattr(value, "hex"):
        try:
            return str(value)
        except TypeError:
            pass
    if isinstance(value, dict):
        return {str(key): _serialize_admin_value(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_serialize_admin_value(item) for item in value]
    return value


def _serialize_model_instance(obj):
    data: dict[str, object] = {}
    for field in obj._meta.concrete_fields:
        data[field.name] = _serialize_admin_value(field.value_from_object(obj))
    return data


def _json_export_response(*, model_label: str, records: list[dict[str, object]]) -> HttpResponse:
    payload = {
        "model": model_label,
        "count": len(records),
        "records": records,
    }
    response = HttpResponse(
        json.dumps(payload, indent=2, sort_keys=True),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="{model_label.lower()}_export.json"'
    return response


@admin.register(ToolGroup)
class ToolGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ("tool_group", "name", "description", "required_parameters_display", "risk", "requires_approval", "released", "updated_at_display")
    list_filter = ("tool_group", "risk", "requires_approval", "released")
    search_fields = ("name", "slug", "tool_group__name")
    actions = ("import_schemas", "export_tools_to_json")

    def required_parameters_display(self, obj: Tool) -> str:
        return ", ".join(obj.required_parameters or [])
    required_parameters_display.short_description = "Required params"

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: Tool) -> str:
        return format_datetime_eastern(obj.updated_at)

    @admin.action(description="Import Schemas")
    def import_schemas(self, request, queryset):
        call_command("seed_tools")
        self.message_user(
            request,
            "Imported tool schemas from the global registry via seed_tools.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Export Tools to JSON")
    def export_tools_to_json(self, request, queryset):
        records: list[dict[str, object]] = []
        for tool in queryset.select_related("tool_group").order_by("tool_group__name", "name"):
            row = _serialize_model_instance(tool)
            row["tool_group_detail"] = {
                "id": str(tool.tool_group_id),
                "name": tool.tool_group.name,
                "description": tool.tool_group.description,
            }
            records.append(row)
        return _json_export_response(model_label="Tool", records=records)


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("tool", "description", "workspace", "default_risk_level", "enabled", "default_requires_approval")
    list_filter = ("workspace", "default_risk_level", "enabled")
    search_fields = ("tool__name", "workspace__name")
    actions = ("sync_to_tools", "export_tools_to_json")

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

    @admin.action(description="Export Tools to JSON")
    def export_tools_to_json(self, request, queryset):
        records: list[dict[str, object]] = []
        definitions = queryset.select_related("tool", "workspace", "tool__tool_group").order_by(
            "workspace__name",
            "tool__name",
            "name",
        )
        for definition in definitions:
            row = _serialize_model_instance(definition)
            row["workspace_detail"] = {
                "id": str(definition.workspace_id),
                "name": definition.workspace.name,
            }
            if definition.tool_id:
                row["tool_detail"] = {
                    "id": str(definition.tool_id),
                    "name": definition.tool.name,
                    "slug": definition.tool.slug,
                    "tool_group": definition.tool.tool_group.name,
                }
            else:
                row["tool_detail"] = None
            records.append(row)
        return _json_export_response(model_label="ToolDefinition", records=records)


@admin.register(AgentToolGrant)
class AgentToolGrantAdmin(admin.ModelAdmin):
    list_display = ("agent", "tool", "enabled", "created_at_display")
    list_filter = ("enabled", "tool")
    search_fields = ("agent__name", "tool__name")

    @admin.display(description="Created At")
    def created_at_display(self, obj: AgentToolGrant) -> str:
        return format_datetime_eastern(obj.created_at)


@admin.register(ToolApprovalGrant)
class ToolApprovalGrantAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "run", "scope_type", "scope_path", "created_by", "revoked_at_display")
    list_filter = ("tool_name", "scope_type", "revoked_at")
    search_fields = ("tool_name", "scope_path", "run__id")

    @admin.display(description="Revoked At")
    def revoked_at_display(self, obj: ToolApprovalGrant) -> str:
        return format_datetime_eastern(obj.revoked_at)


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "tool_name", "status", "requires_approval", "approval_grant", "run_link")
    list_filter = ("status", "requires_approval")
    search_fields = ("id", "tool_name", "run__id")

    def run_link(self, obj: ToolCall) -> str:
        url = reverse("ui:run_detail", kwargs={"run_id": obj.run_id})
        return format_html('<a href="{}">{}</a>', url, obj.run_id)
    run_link.short_description = "Run"
