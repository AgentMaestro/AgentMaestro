from django.contrib import admin

from core.admin_utils import format_datetime_eastern

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
    list_display = ("transport", "kind", "paired_agents")
    list_filter = ("transport", "kind")
    search_fields = ("transport__key", "kind", "pairings__agent__name", "conversations__control_conversation__default_for_agents__name")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.prefetch_related(
            "pairings__agent",
            "conversations__control_conversation__default_for_agents",
        )

    @admin.display(description="Paired Agent")
    def paired_agents(self, obj: TransportEndpoint) -> str:
        names: set[str] = set()
        for pairing in obj.pairings.all():
            agent = getattr(pairing, "agent", None)
            if agent and agent.name:
                names.add(agent.name)
        for conversation in obj.conversations.all():
            control_conversation = getattr(conversation, "control_conversation", None)
            if not control_conversation:
                continue
            for agent in control_conversation.default_for_agents.all():
                if agent.name:
                    names.add(agent.name)
        if not names:
            return "-"
        return ", ".join(sorted(names))


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
    list_display = (
        "pair_code",
        "endpoint",
        "agent",
        "status",
        "created_at_display",
        "expires_at_display",
    )
    list_filter = ("endpoint", "status")
    search_fields = ("pair_code", "claimed_chat_id")

    @admin.display(description="Created At")
    def created_at_display(self, obj: PendingPairing) -> str:
        return format_datetime_eastern(obj.created_at)

    @admin.display(description="Expires At")
    def expires_at_display(self, obj: PendingPairing) -> str:
        return format_datetime_eastern(obj.expires_at)


@admin.register(CommsMessage)
class CommsMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "direction", "created_at_display")
    list_filter = ("direction", "conversation__transport")
    search_fields = ("text",)

    @admin.display(description="Created At")
    def created_at_display(self, obj: CommsMessage) -> str:
        return format_datetime_eastern(obj.created_at)


@admin.register(RemoteApprovalTicket)
class RemoteApprovalTicketAdmin(admin.ModelAdmin):
    list_display = (
        "short_code",
        "transport",
        "tool_call",
        "status",
        "expires_at_display",
        "acted_by_label",
    )
    list_filter = ("transport", "status")
    search_fields = ("short_code", "external_chat_id", "acted_by_label", "tool_call__tool_name")

    @admin.display(description="Expires At")
    def expires_at_display(self, obj: RemoteApprovalTicket) -> str:
        return format_datetime_eastern(obj.expires_at)
