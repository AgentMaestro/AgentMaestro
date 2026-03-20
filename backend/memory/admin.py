from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from core.admin_utils import format_datetime_eastern
from memory.models import MemoryHealthSnapshot, MemoryRecord, RecurrenceRule, ScheduledTask, ScheduledTaskApproval


@admin.register(MemoryRecord)
class MemoryRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "scope_type",
        "scope_id",
        "memory_kind",
        "dedupe_key",
        "source_kind",
        "pinned",
        "importance",
        "access_count",
        "last_accessed_at_display",
        "expires_at_display",
        "updated_at_display",
    )
    list_filter = ("scope_type", "memory_kind", "source_kind", "pinned")
    search_fields = ("scope_id", "dedupe_key", "source_ref", "content", "summary")
    ordering = ("-pinned", "-updated_at")

    @admin.display(description="Last Accessed")
    def last_accessed_at_display(self, obj: MemoryRecord) -> str:
        return format_datetime_eastern(obj.last_accessed_at)

    @admin.display(description="Expires At")
    def expires_at_display(self, obj: MemoryRecord) -> str:
        return format_datetime_eastern(obj.expires_at)

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: MemoryRecord) -> str:
        return format_datetime_eastern(obj.updated_at)


@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name_or_summary",
        "frequency",
        "timezone",
        "window_display",
        "local_time_display",
        "scheduled_task_count",
        "is_active",
        "updated_at_display",
    )
    list_filter = ("frequency", "timezone", "is_active")
    search_fields = ("name", "timezone")
    ordering = ("-updated_at",)
    readonly_fields = ("summary", "scheduled_task_count")

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: RecurrenceRule) -> str:
        return format_datetime_eastern(obj.updated_at)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_scheduled_task_count=Count("scheduled_tasks"))

    @admin.display(description="Rule")
    def name_or_summary(self, obj: RecurrenceRule):
        return obj.name or obj.summary

    @admin.display(description="Summary")
    def summary(self, obj: RecurrenceRule):
        if obj is None:
            return "-"
        return obj.summary

    @admin.display(description="Window")
    def window_display(self, obj: RecurrenceRule):
        if obj.window_start_time and obj.window_end_time:
            return f"{obj.window_start_time.isoformat(timespec='minutes')} - {obj.window_end_time.isoformat(timespec='minutes')}"
        return "-"

    @admin.display(description="Local Time")
    def local_time_display(self, obj: RecurrenceRule):
        if obj.local_time is not None:
            return obj.local_time.isoformat(timespec="minutes")
        if obj.run_minute is not None:
            return f":{obj.run_minute:02d}"
        return "-"

    @admin.display(description="Tasks")
    def scheduled_task_count(self, obj: RecurrenceRule):
        if obj is None:
            return 0
        return getattr(obj, "_scheduled_task_count", obj.scheduled_tasks.count())


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "task_type",
        "execution_mode",
        "enabled",
        "integration_kind_short",
        "resource_kind_short",
        "account_scope_short",
        "recurrence_frequency",
        "recurrence_timezone",
        "recurrence_window",
        "recurrence_summary_short",
        "next_run_at_display",
        "last_run_at_display",
        "last_result_summary_short",
        "last_error_short",
        "last_run",
        "active_run",
        "failure_count",
    )
    list_filter = ("task_type", "execution_mode", "enabled", "recurrence_rule__timezone", "recurrence_rule__frequency")
    search_fields = ("title", "agent__name", "owner__username", "last_result_summary", "last_error")
    readonly_fields = ("recurrence_detail", "google_intent_detail")

    @admin.display(description="Next Run")
    def next_run_at_display(self, obj: ScheduledTask) -> str:
        return format_datetime_eastern(obj.next_run_at)

    @admin.display(description="Last Run")
    def last_run_at_display(self, obj: ScheduledTask) -> str:
        return format_datetime_eastern(obj.last_run_at)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "agent",
            "owner",
            "workspace",
            "recurrence_rule",
            "last_run",
            "active_run",
        )

    @admin.display(description="Frequency")
    def recurrence_frequency(self, obj: ScheduledTask):
        return obj.recurrence_rule.get_frequency_display()

    @admin.display(description="Timezone")
    def recurrence_timezone(self, obj: ScheduledTask):
        return obj.recurrence_rule.timezone

    @admin.display(description="Window")
    def recurrence_window(self, obj: ScheduledTask):
        rule = obj.recurrence_rule
        if rule.window_start_time and rule.window_end_time:
            return f"{rule.window_start_time.isoformat(timespec='minutes')} - {rule.window_end_time.isoformat(timespec='minutes')}"
        return "-"

    @admin.display(description="Recurrence")
    def recurrence_summary_short(self, obj: ScheduledTask):
        return obj.recurrence_summary

    @admin.display(description="Integration")
    def integration_kind_short(self, obj: ScheduledTask):
        return str(obj.execution_payload.get("integration_kind") or "-").strip() or "-"

    @admin.display(description="Resource")
    def resource_kind_short(self, obj: ScheduledTask):
        return str(obj.execution_payload.get("resource_kind") or "-").strip() or "-"

    @admin.display(description="Scope")
    def account_scope_short(self, obj: ScheduledTask):
        return str(obj.execution_payload.get("account_scope") or "-").strip() or "-"

    @admin.display(description="Last Result")
    def last_result_summary_short(self, obj: ScheduledTask):
        text = str(obj.last_result_summary or "").strip()
        return text[:90] + ("..." if len(text) > 90 else "") if text else "-"

    @admin.display(description="Last Error")
    def last_error_short(self, obj: ScheduledTask):
        text = str(obj.last_error or "").strip()
        return text[:90] + ("..." if len(text) > 90 else "") if text else "-"

    @admin.display(description="Recurrence Details")
    def recurrence_detail(self, obj: ScheduledTask):
        if obj is None:
            return "-"
        rule = obj.recurrence_rule
        lines = [
            format_html("<strong>{}</strong>", rule.summary),
            f"Frequency: {rule.get_frequency_display()}",
            f"Timezone: {rule.timezone}",
        ]
        if rule.local_time is not None:
            lines.append(f"Local time: {rule.local_time.isoformat(timespec='minutes')}")
        if rule.run_minute is not None:
            lines.append(f"Run minute: {rule.run_minute:02d}")
        if rule.window_start_time and rule.window_end_time:
            lines.append(
                f"Window: {rule.window_start_time.isoformat(timespec='minutes')} - {rule.window_end_time.isoformat(timespec='minutes')}"
            )
        if rule.by_weekday:
            lines.append(f"Weekdays: {', '.join(rule.by_weekday)}")
        if rule.by_month_day:
            lines.append(f"Month days: {', '.join(str(day) for day in rule.by_month_day)}")
        if rule.week_of_month is not None and rule.weekday_of_month:
            lines.append(f"Week-of-month: {rule.week_of_month} / {rule.weekday_of_month}")
        if rule.by_month:
            lines.append(f"Months: {', '.join(str(month) for month in rule.by_month)}")
        return format_html_join(mark_safe("<br>"), "{}", ((line,) for line in lines))

    @admin.display(description="Google Intent")
    def google_intent_detail(self, obj: ScheduledTask):
        payload = dict(obj.execution_payload or {})
        lines = [
            "Google intent summary",
            f"integration_kind: {payload.get('integration_kind', '-') or '-'}",
            f"resource_kind: {payload.get('resource_kind', '-') or '-'}",
            f"action_kind: {payload.get('action_kind', '-') or '-'}",
            f"operation: {payload.get('operation', '-') or '-'}",
            f"account_scope: {payload.get('account_scope', '-') or '-'}",
        ]
        steps = list(payload.get("steps") or [])
        if steps:
            lines.append(f"steps: {len(steps)}")
            for index, step in enumerate(steps, start=1):
                lines.append(
                    f"step {index}: {step.get('resource_kind', '-') or '-'} / {step.get('action_kind', '-') or '-'} / {step.get('operation', '-') or '-'} / scope={step.get('account_scope', '-') or '-'}"
                )
        elif payload.get("integration_kind") == "google":
            lines.append("steps: 1 (implicit)")
        else:
            lines.append("steps: -")
        return format_html_join(mark_safe("<br>"), "{}", ((line,) for line in lines))


@admin.register(ScheduledTaskApproval)
class ScheduledTaskApprovalAdmin(admin.ModelAdmin):
    list_display = (
        "scheduled_task",
        "fingerprint_short",
        "fingerprint_version",
        "approved_by",
        "approved_at_display",
        "expires_at_display",
        "revoked_at_display",
        "use_count",
        "last_used_at_display",
    )
    list_filter = ("task_type", "execution_mode", "approved_at", "expires_at", "revoked_at")
    search_fields = ("scheduled_task__title", "fingerprint", "approved_by__username")
    ordering = ("-approved_at",)

    @admin.display(description="Approved At")
    def approved_at_display(self, obj: ScheduledTaskApproval) -> str:
        return format_datetime_eastern(obj.approved_at)

    @admin.display(description="Expires At")
    def expires_at_display(self, obj: ScheduledTaskApproval) -> str:
        return format_datetime_eastern(obj.expires_at)

    @admin.display(description="Revoked At")
    def revoked_at_display(self, obj: ScheduledTaskApproval) -> str:
        return format_datetime_eastern(obj.revoked_at)

    @admin.display(description="Last Used")
    def last_used_at_display(self, obj: ScheduledTaskApproval) -> str:
        return format_datetime_eastern(obj.last_used_at)

    @admin.display(description="Fingerprint")
    def fingerprint_short(self, obj: ScheduledTaskApproval):
        return obj.fingerprint[:12]


@admin.register(MemoryHealthSnapshot)
class MemoryHealthSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "created_at_display",
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

    @admin.display(description="Created At")
    def created_at_display(self, obj: MemoryHealthSnapshot) -> str:
        return format_datetime_eastern(obj.created_at)

