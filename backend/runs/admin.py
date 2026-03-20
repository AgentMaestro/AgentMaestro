from django.contrib import admin

from core.admin_utils import format_datetime_eastern

from .models import AgentRun, AgentStep, Artifact, RunEvent, RunMemory


class AgentStepInline(admin.TabularInline):
    model = AgentStep
    fields = ("step_index", "kind", "created_at_display", "payload")
    readonly_fields = ("step_index", "kind", "created_at_display", "payload")
    ordering = ("step_index",)
    extra = 0

    @admin.display(description="Created At")
    def created_at_display(self, obj: AgentStep) -> str:
        return format_datetime_eastern(obj.created_at)


@admin.register(AgentStep)
class AgentStepAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "step_index", "kind", "created_at_display")
    list_filter = ("kind", "created_at")
    search_fields = ("id", "run__id", "run__agent__name", "payload")
    readonly_fields = ("created_at_display", "updated_at_display")
    ordering = ("-created_at",)

    @admin.display(description="Created At")
    def created_at_display(self, obj: AgentStep) -> str:
        return format_datetime_eastern(obj.created_at)

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: AgentStep) -> str:
        return format_datetime_eastern(obj.updated_at)


class RunEventInline(admin.TabularInline):
    model = RunEvent
    fields = ("seq", "event_type", "payload", "created_at_display")
    readonly_fields = ("seq", "event_type", "payload", "created_at_display")
    ordering = ("seq",)
    extra = 0

    @admin.display(description="Created At")
    def created_at_display(self, obj: RunEvent) -> str:
        return format_datetime_eastern(obj.created_at)


class RunMemoryInline(admin.StackedInline):
    model = RunMemory
    fields = (
        "objective",
        "current_plan",
        "key_facts",
        "open_questions",
        "recent_tool_results",
        "notes",
        "created_at_display",
        "updated_at_display",
    )
    readonly_fields = ("created_at_display", "updated_at_display")
    extra = 0
    can_delete = False
    max_num = 1

    @admin.display(description="Created At")
    def created_at_display(self, obj: RunMemory) -> str:
        return format_datetime_eastern(obj.created_at)

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: RunMemory) -> str:
        return format_datetime_eastern(obj.updated_at)


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "workspace",
        "agent",
        "status",
        "execution_mode",
        "trigger_kind",
        "trigger_ref",
        "approval_mode",
        "started_at_display",
    )
    list_filter = ("workspace", "status", "execution_mode", "trigger_kind", "approval_mode", "started_at")
    search_fields = ("id", "trigger_ref", "delivery_target", "approval_fingerprint", "approval_source_ref")
    inlines = [RunMemoryInline, AgentStepInline, RunEventInline]

    @admin.display(description="Started At")
    def started_at_display(self, obj: AgentRun) -> str:
        return format_datetime_eastern(obj.started_at)


@admin.register(RunMemory)
class RunMemoryAdmin(admin.ModelAdmin):
    list_display = ("run", "run_agent", "run_status", "updated_at_display")
    list_filter = ("run__workspace", "run__agent", "run__status", "updated_at")
    search_fields = ("run__id", "run__agent__name", "objective", "current_plan", "notes")
    readonly_fields = ("created_at_display", "updated_at_display")

    @admin.display(description="Agent")
    def run_agent(self, obj: RunMemory):
        return obj.run.agent

    @admin.display(description="Run Status")
    def run_status(self, obj: RunMemory):
        return obj.run.status

    @admin.display(description="Created At")
    def created_at_display(self, obj: RunMemory) -> str:
        return format_datetime_eastern(obj.created_at)

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: RunMemory) -> str:
        return format_datetime_eastern(obj.updated_at)


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "type")
    list_filter = ("type",)
