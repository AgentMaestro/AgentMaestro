from django.contrib import admin
from .models import LLMModelProfile, LLMRun, LLMMessage, LLMToolCall


@admin.register(LLMModelProfile)
class LLMModelProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "agent_role", "provider", "model", "is_active", "updated_at")
    list_filter = ("provider", "agent_role", "is_active")
    search_fields = ("name", "model")


@admin.register(LLMRun)
class LLMRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "agent_name",
        "provider",
        "model",
        "status",
        "created_at",
        "profile",
    )
    list_filter = ("provider", "status", "profile")
    search_fields = ("id", "agent_name", "model")
    readonly_fields = ("created_at",)


@admin.register(LLMMessage)
class LLMMessageAdmin(admin.ModelAdmin):
    list_display = ("run", "role", "name", "created_at")
    list_filter = ("role",)
    search_fields = ("run__id", "content", "name")
    readonly_fields = ("created_at",)


@admin.register(LLMToolCall)
class LLMToolCallAdmin(admin.ModelAdmin):
    list_display = ("run", "tool_name", "success", "created_at")
    list_filter = ("tool_name", "success")
    search_fields = ("run__id", "tool_name")
    readonly_fields = ("created_at",)
