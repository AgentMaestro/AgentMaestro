from django.contrib import admin

from .models import (
    Role,
    Operator,
    ControlConversation,
    ControlMessage,
    IngestEvent,
    ApprovalRequest,
    ApprovalGrant,
)
from .models import ApprovalRequest, ApprovalGrant


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
    list_display = ("uuid", "title", "kind", "updated_at")
    list_filter = ("kind", "comms_conversation__transport__key")


@admin.register(ControlMessage)
class ControlMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "author_label", "direction", "created_at")
    list_filter = ("direction", "author_type", "source_transport")
    search_fields = ("text", "author_label")


@admin.register(IngestEvent)
class IngestEventAdmin(admin.ModelAdmin):
    list_display = ("transport", "external_event_id", "received_at")
    search_fields = ("transport", "external_event_id")


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("uuid", "tool_name", "risk_level", "status", "created_at")
    list_filter = ("status", "risk_level")
    search_fields = ("tool_name",)


@admin.register(ApprovalGrant)
class ApprovalGrantAdmin(admin.ModelAdmin):
    list_display = ("scope", "is_persistent", "expires_at", "created_at")
    search_fields = ("scope",)
