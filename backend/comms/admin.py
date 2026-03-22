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
    list_display = ("transport", "kind", "bot_username", "paired_agents", "polling_status")
    list_filter = ("transport", "kind")
    search_fields = ("transport__key", "kind", "pairings__agent__name", "conversations__control_conversation__default_for_agents__name")
    readonly_fields = ("bot_username", "paired_agents", "paired_chat_ids", "polling_status", "config_preview")
    fieldsets = (
        (
            None,
            {
                "fields": ("transport", "kind", "bot_username", "config_preview"),
            },
        ),
        (
            "Pairing",
            {
                "fields": ("paired_agents", "paired_chat_ids", "polling_status"),
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.prefetch_related(
            "pairings__agent",
            "conversations__control_conversation__default_for_agents",
        )

    @admin.display(description="Telegram Bot")
    def bot_username(self, obj: TransportEndpoint) -> str:
        config = obj.config or {}
        username = str(config.get("bot_username") or "").strip()
        bot_name = str(config.get("bot_name") or "").strip()
        parts = []
        if username:
            parts.append(f"@{username}")
        if bot_name:
            parts.append(bot_name)
        return " ".join(parts) if parts else "-"

    @admin.display(description="Paired Agent")
    def paired_agents(self, obj: TransportEndpoint) -> str:
        details: list[str] = []
        seen: set[str] = set()
        for pairing in obj.pairings.all():
            agent = getattr(pairing, "agent", None)
            if agent and agent.name:
                agent_name = agent.name
                if agent_name not in seen:
                    seen.add(agent_name)
                    chat_id = str(getattr(pairing, "claimed_chat_id", "") or "").strip()
                    if chat_id:
                        details.append(f"{agent_name} (chat {chat_id})")
                    else:
                        details.append(agent_name)
        for conversation in obj.conversations.all():
            control_conversation = getattr(conversation, "control_conversation", None)
            if not control_conversation:
                continue
            for agent in control_conversation.default_for_agents.all():
                if agent.name:
                    agent_name = agent.name
                    if agent_name not in seen:
                        seen.add(agent_name)
                        details.append(agent_name)
        if not details:
            return "-"
        return ", ".join(sorted(details))

    @admin.display(description="Claimed Chats")
    def paired_chat_ids(self, obj: TransportEndpoint) -> str:
        rows: list[str] = []
        for pairing in obj.pairings.all():
            agent = getattr(pairing, "agent", None)
            chat_id = str(getattr(pairing, "claimed_chat_id", "") or "").strip()
            status = str(getattr(pairing, "status", "") or "").strip()
            if not agent and not chat_id:
                continue
            agent_name = agent.name if agent and agent.name else "-"
            chat_text = chat_id or "-"
            status_text = status or "-"
            rows.append(f"{agent_name}: {chat_text} ({status_text})")
        return "\n".join(rows) if rows else "-"

    @admin.display(description="Config Preview")
    def config_preview(self, obj: TransportEndpoint) -> str:
        config = obj.config or {}
        if not config:
            return "-"
        redacted = dict(config)
        if redacted.get("bot_token"):
            token = str(redacted["bot_token"])
            redacted["bot_token"] = token[:6] + "..." + token[-4:] if len(token) > 10 else "..."
        if redacted.get("webhook_secret"):
            secret = str(redacted["webhook_secret"])
            redacted["webhook_secret"] = secret[:6] + "..." + secret[-4:] if len(secret) > 10 else "..."
        return str(redacted)

    @admin.display(description="Polling Status")
    def polling_status(self, obj: TransportEndpoint) -> str:
        config = obj.config or {}
        if config.get("telegram_polling_disabled"):
            reason = str(config.get("telegram_polling_disabled_reason") or "disabled").strip()
            at = str(config.get("telegram_polling_disabled_at") or "").strip()
            return f"disabled ({reason}{', ' + at if at else ''})"
        return "enabled"


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
