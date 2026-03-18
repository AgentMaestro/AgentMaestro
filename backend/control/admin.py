from django.contrib import admin

from core.admin_utils import format_datetime_eastern

from .models import (
    Role,
    Operator,
    ControlConversation,
    ControlMessage,
    IngestEvent,
    ApprovalRequest,
    ApprovalGrant,
)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("key", "name")
    search_fields = ("key", "name")


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ("user", "is_active")
    search_fields = ("user__username",)
    filter_horizontal = ("roles",)


@admin.register(ControlConversation)
class ControlConversationAdmin(admin.ModelAdmin):
    list_display = ("uuid", "title", "kind", "updated_at_display")
    list_filter = ("kind", "comms_conversation__transport__key")

    @admin.display(description="Updated At")
    def updated_at_display(self, obj: ControlConversation) -> str:
        return format_datetime_eastern(obj.updated_at)


@admin.register(ControlMessage)
class ControlMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "author_label", "direction", "created_at_display")
    list_filter = ("direction", "author_type", "source_transport")
    search_fields = ("text", "author_label")

    @admin.display(description="Created At")
    def created_at_display(self, obj: ControlMessage) -> str:
        return format_datetime_eastern(obj.created_at)


@admin.register(IngestEvent)
class IngestEventAdmin(admin.ModelAdmin):
    list_display = ("transport", "external_event_id", "received_at_display")
    search_fields = ("transport", "external_event_id")

    @admin.display(description="Received At")
    def received_at_display(self, obj: IngestEvent) -> str:
        return format_datetime_eastern(obj.received_at)


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("uuid", "tool_name", "risk_level", "status", "created_at_display")
    list_filter = ("status", "risk_level")
    search_fields = ("tool_name",)

    @admin.display(description="Created At")
    def created_at_display(self, obj: ApprovalRequest) -> str:
        return format_datetime_eastern(obj.created_at)


@admin.register(ApprovalGrant)
class ApprovalGrantAdmin(admin.ModelAdmin):
    list_display = ("scope", "is_persistent", "expires_at_display", "created_at_display")
    search_fields = ("scope",)

    @admin.display(description="Expires At")
    def expires_at_display(self, obj: ApprovalGrant) -> str:
        return format_datetime_eastern(obj.expires_at)

    @admin.display(description="Created At")
    def created_at_display(self, obj: ApprovalGrant) -> str:
        return format_datetime_eastern(obj.created_at)
