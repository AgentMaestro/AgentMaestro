from django.contrib import admin

from .models import GoogleAccount


@admin.register(GoogleAccount)
class GoogleAccountAdmin(admin.ModelAdmin):
    list_display = (
        "workspace",
        "owner",
        "email",
        "google_subject",
        "is_active",
        "token_expires_at",
        "last_synced_at",
        "last_error_short",
    )
    list_filter = ("is_active", "workspace")
    search_fields = ("email", "google_subject", "owner__username", "workspace__name")
    readonly_fields = ("last_synced_at", "last_error", "metadata")

    @admin.display(description="Last Error")
    def last_error_short(self, obj: GoogleAccount):
        text = str(obj.last_error or "").strip()
        return text[:80] + ("..." if len(text) > 80 else "") if text else "-"
