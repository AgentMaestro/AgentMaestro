from django.contrib import admin

from .models import (
    ExternalIdentity,
    PendingPairing,
    Transport,
    TransportEndpoint,
    CommsConversation,
    CommsMessage,
    RemoteApprovalTicket,
)


@admin.register(Transport)
class TransportAdmin(admin.ModelAdmin):
    list_display = ("key", "display_name", "mode", "is_enabled")
    list_filter = ("mode", "is_enabled")
    search_fields = ("key", "display_name")


@admin.register(TransportEndpoint)
class TransportEndpointAdmin(admin.ModelAdmin):
    list_display = ("transport", "kind")
    list_filter = ("transport", "kind")
    search_fields = ("transport__key", "kind")


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    list_display = ("transport", "external_user_id", "is_allowed")
    search_fields = ("external_user_id", "username", "display_name")
    list_filter = ("transport", "is_allowed")


@admin.register(CommsConversation)
class CommsConversationAdmin(admin.ModelAdmin):
    list_display = ("transport", "endpoint", "external_conversation_id", "title")
    list_filter = ("transport", "endpoint__kind", "control_conversation__kind")
    search_fields = ("external_conversation_id", "title")


@admin.register(PendingPairing)
class PendingPairingAdmin(admin.ModelAdmin):
    list_display = ("pair_code", "endpoint", "agent", "status", "created_at", "expires_at")
    list_filter = ("endpoint", "status")
    search_fields = ("pair_code", "claimed_chat_id")


@admin.register(CommsMessage)
class CommsMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "direction", "created_at")
    list_filter = ("direction", "conversation__transport")
    search_fields = ("text",)

@admin.register(RemoteApprovalTicket)
class RemoteApprovalTicketAdmin(admin.ModelAdmin):
    list_display = ("short_code", "transport", "tool_call", "status", "expires_at", "acted_by_label")
    list_filter = ("transport", "status")
    search_fields = ("short_code", "external_chat_id", "acted_by_label", "tool_call__tool_name")

