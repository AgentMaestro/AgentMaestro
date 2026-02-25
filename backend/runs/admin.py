from django.contrib import admin

from .models import AgentRun, AgentStep, RunEvent, Artifact


class AgentStepInline(admin.TabularInline):
    model = AgentStep
    fields = ("step_index", "kind", "created_at", "payload")
    readonly_fields = ("step_index", "kind", "created_at", "payload")
    ordering = ("step_index",)
    extra = 0


class RunEventInline(admin.TabularInline):
    model = RunEvent
    fields = ("seq", "event_type", "payload", "created_at")
    readonly_fields = ("seq", "event_type", "payload", "created_at")
    ordering = ("seq",)
    extra = 0


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workspace", "agent", "status", "started_at")
    list_filter = ("workspace", "status", "started_at")
    search_fields = ("id",)
    inlines = [AgentStepInline, RunEventInline]


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "type")
    list_filter = ("type",)
