from django.contrib import admin

from memory.models import MemoryHealthSnapshot, MemoryRecord, ScheduledTask


@admin.register(MemoryRecord)
class MemoryRecordAdmin(admin.ModelAdmin):
    list_display = (
        "scope_type",
        "scope_id",
        "memory_kind",
        "dedupe_key",
        "source_kind",
        "pinned",
        "importance",
        "access_count",
        "last_accessed_at",
        "expires_at",
        "updated_at",
    )
    list_filter = ("scope_type", "memory_kind", "source_kind", "pinned")
    search_fields = ("scope_id", "dedupe_key", "source_ref", "content", "summary")
    ordering = ("-pinned", "-updated_at")


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "task_type",
        "agent",
        "owner",
        "enabled",
        "timezone",
        "local_time",
        "next_run_at",
        "last_run_at",
        "failure_count",
    )
    list_filter = ("task_type", "enabled", "timezone")
    search_fields = ("title", "agent__name", "owner__username", "last_result_summary", "last_error")


@admin.register(MemoryHealthSnapshot)
class MemoryHealthSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "retention_days",
        "compare_days",
        "total_records",
        "pinned_records",
        "high_importance_records",
        "retention_candidate_records",
        "scheduled_tasks_total",
    )
    list_filter = ("retention_days", "compare_days")
    search_fields = ("created_at",)
    ordering = ("-created_at",)
