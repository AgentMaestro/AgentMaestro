from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import ToolDefinition, ToolCall


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "default_risk_level", "enabled")
    list_filter = ("workspace", "default_risk_level", "enabled")
    search_fields = ("name", "workspace__name")


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "tool_name", "status", "requires_approval", "run_link")
    list_filter = ("status", "requires_approval")
    search_fields = ("id", "tool_name", "run__id")

    def run_link(self, obj: ToolCall) -> str:
        url = reverse("ui:run_detail", kwargs={"run_id": obj.run_id})
        return format_html('<a href="{}">{}</a>', url, obj.run_id)
    run_link.short_description = "Run"
