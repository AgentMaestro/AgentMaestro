from django.contrib import admin

from .models import AgentRun, AgentStep, Artifact, RunEvent, RunMemory


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


class RunMemoryInline(admin.StackedInline):
    model = RunMemory
    fields = (
        "objective",
        "current_plan",
        "key_facts",
        "open_questions",
        "recent_tool_results",
        "notes",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")
    extra = 0
    can_delete = False
    max_num = 1


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workspace", "agent", "status", "started_at")
    list_filter = ("workspace", "status", "started_at")
    search_fields = ("id",)
    inlines = [RunMemoryInline, AgentStepInline, RunEventInline]


@admin.register(RunMemory)
class RunMemoryAdmin(admin.ModelAdmin):
    list_display = ("run", "run_agent", "run_status", "updated_at")
    list_filter = ("run__workspace", "run__agent", "run__status", "updated_at")
    search_fields = ("run__id", "run__agent__name", "objective", "current_plan", "notes")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Agent")
    def run_agent(self, obj: RunMemory):
        return obj.run.agent

    @admin.display(description="Run Status")
    def run_status(self, obj: RunMemory):
        return obj.run.status


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "type")
    list_filter = ("type",)
