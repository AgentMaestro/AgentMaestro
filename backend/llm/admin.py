from django.contrib import admin

from .models import LLMModelProfile, LLMRun, LLMMessage, LLMToolCall, ModelsAvailable


class RunIDFilter(admin.SimpleListFilter):
    title = "Run ID"
    parameter_name = "run_id"
    template = "admin/uuid_filter.html"

    def lookups(self, request, model_admin):
        return (("any", "Any"),)

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(id=value)


@admin.register(LLMModelProfile)
class LLMModelProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "agent_role", "provider", "model", "temperature", "is_active", "updated_at")
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
    list_filter = (RunIDFilter, "provider", "status", "profile")
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


@admin.register(ModelsAvailable)
class ModelsAvailableAdmin(admin.ModelAdmin):
    list_display = ("company", "api", "name", "updated_at")
    list_filter = ("company", "api")
    search_fields = ("company", "name")
    readonly_fields = ("created_at",)
