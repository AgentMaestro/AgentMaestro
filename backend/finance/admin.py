from django.contrib import admin

from .models import (
    FinanceDataCacheEntry,
    FinanceResearchSnapshot,
    Portfolio,
    Position,
    SchwabOAuthCredential,
    Ticker,
    Watchlist,
    WatchlistItem,
)


@admin.register(Ticker)
class TickerAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "exchange", "asset_type", "currency", "is_active")
    search_fields = ("symbol", "name", "exchange")
    list_filter = ("asset_type", "currency", "is_active")


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "owner", "base_currency", "is_default", "created_at")
    search_fields = ("name", "workspace__name", "owner__username")
    list_filter = ("base_currency", "is_default")


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "owner", "is_default", "created_at")
    search_fields = ("name", "workspace__name", "owner__username")
    list_filter = ("is_default",)


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("watchlist", "ticker", "added_by", "created_at")
    search_fields = ("watchlist__name", "ticker__symbol", "ticker__name")


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "ticker", "side", "quantity", "average_cost", "cost_basis")
    search_fields = ("portfolio__name", "ticker__symbol")
    list_filter = ("side",)


@admin.register(FinanceDataCacheEntry)
class FinanceDataCacheEntryAdmin(admin.ModelAdmin):
    list_display = ("cache_key", "data_kind", "ticker", "portfolio", "watchlist", "updated_at", "expires_at", "created_at")
    search_fields = ("cache_key", "ticker__symbol", "source_name")
    list_filter = ("data_kind", "source_name")


@admin.register(FinanceResearchSnapshot)
class FinanceResearchSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "snapshot_key",
        "snapshot_kind",
        "workspace",
        "ticker",
        "portfolio",
        "watchlist",
        "expires_at",
        "created_at",
    )
    search_fields = ("snapshot_key", "ticker__symbol", "portfolio__name", "watchlist__name")
    list_filter = ("snapshot_kind",)


@admin.register(SchwabOAuthCredential)
class SchwabOAuthCredentialAdmin(admin.ModelAdmin):
    list_display = ("workspace", "owner", "is_active", "primary_account_hash", "expires_at", "created_at")
    search_fields = ("workspace__name", "owner__username", "primary_account_hash")
    list_filter = ("is_active",)
