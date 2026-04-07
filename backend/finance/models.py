from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel, Workspace


class Ticker(TimeStampedModel):
    class AssetType(models.TextChoices):
        EQUITY = "EQUITY", "Equity"
        ETF = "ETF", "ETF"
        INDEX = "INDEX", "Index"
        OPTION = "OPTION", "Option"
        FUND = "FUND", "Fund"
        CRYPTO = "CRYPTO", "Crypto"
        OTHER = "OTHER", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    symbol = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=180, blank=True, default="")
    exchange = models.CharField(max_length=64, blank=True, default="")
    asset_type = models.CharField(max_length=16, choices=AssetType.choices, default=AssetType.EQUITY)
    currency = models.CharField(max_length=8, default="USD")
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["symbol"]),
            models.Index(fields=["exchange", "asset_type"]),
        ]

    def __str__(self) -> str:
        return self.symbol


class Portfolio(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="finance_portfolios",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_portfolios",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    base_currency = models.CharField(max_length=8, default="USD")
    is_default = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="finance_portfolio_workspace_name_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "is_default"]),
            models.Index(fields=["owner", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.workspace}: {self.name}"


class Watchlist(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="finance_watchlists",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_watchlists",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="finance_watchlist_workspace_name_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "is_default"]),
            models.Index(fields=["owner", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.workspace}: {self.name}"


class WatchlistItem(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    watchlist = models.ForeignKey(
        Watchlist,
        on_delete=models.CASCADE,
        related_name="items",
    )
    ticker = models.ForeignKey(
        Ticker,
        on_delete=models.PROTECT,
        related_name="watchlist_items",
    )
    note = models.TextField(blank=True, default="")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_watchlist_items_added",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["watchlist", "ticker"],
                name="finance_watchlist_item_watchlist_ticker_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["watchlist", "created_at"]),
            models.Index(fields=["ticker", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.watchlist} -> {self.ticker.symbol}"


class Position(TimeStampedModel):
    class Side(models.TextChoices):
        LONG = "LONG", "Long"
        SHORT = "SHORT", "Short"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.CASCADE,
        related_name="positions",
    )
    ticker = models.ForeignKey(
        Ticker,
        on_delete=models.PROTECT,
        related_name="positions",
    )
    side = models.CharField(max_length=8, choices=Side.choices, default=Side.LONG)
    quantity = models.DecimalField(max_digits=24, decimal_places=8, default=0)
    average_cost = models.DecimalField(max_digits=24, decimal_places=4, default=0)
    cost_basis = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["portfolio", "ticker", "side"],
                name="finance_position_portfolio_ticker_side_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["portfolio", "ticker"]),
            models.Index(fields=["ticker", "side"]),
        ]

    def __str__(self) -> str:
        return f"{self.portfolio} {self.side} {self.ticker.symbol}"


class FinanceDataCacheEntry(TimeStampedModel):
    class DataKind(models.TextChoices):
        QUOTE = "QUOTE", "Quote"
        HISTORY = "HISTORY", "History"
        NEWS = "NEWS", "News"
        FILINGS = "FILINGS", "Filings"
        OPTIONS_CHAIN = "OPTIONS_CHAIN", "Options Chain"
        INDICATOR = "INDICATOR", "Indicator"
        RESEARCH = "RESEARCH", "Research"
        OTHER = "OTHER", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cache_key = models.CharField(max_length=255, unique=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="finance_cache_entries",
    )
    ticker = models.ForeignKey(
        Ticker,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cache_entries",
    )
    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cache_entries",
    )
    watchlist = models.ForeignKey(
        Watchlist,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cache_entries",
    )
    data_kind = models.CharField(max_length=24, choices=DataKind.choices, default=DataKind.OTHER)
    source_name = models.CharField(max_length=64, blank=True, default="")
    timeframe = models.CharField(max_length=32, blank=True, default="")
    as_of = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    summary_text = models.TextField(blank=True, default="")
    response_hash = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["cache_key"]),
            models.Index(fields=["data_kind", "expires_at"]),
            models.Index(fields=["ticker", "data_kind", "timeframe"]),
            models.Index(fields=["workspace", "data_kind"]),
        ]

    def __str__(self) -> str:
        scope = self.ticker.symbol if self.ticker_id else self.cache_key
        return f"{self.data_kind} cache for {scope}"


class FinanceResearchSnapshot(TimeStampedModel):
    class SnapshotKind(models.TextChoices):
        PORTFOLIO = "PORTFOLIO", "Portfolio"
        WATCHLIST = "WATCHLIST", "Watchlist"
        TICKER = "TICKER", "Ticker"
        COMPARISON = "COMPARISON", "Comparison"
        SCREEN = "SCREEN", "Screen"
        OTHER = "OTHER", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot_key = models.CharField(max_length=255, unique=True)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="finance_snapshots",
    )
    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    watchlist = models.ForeignKey(
        Watchlist,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    ticker = models.ForeignKey(
        Ticker,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="snapshots",
    )
    snapshot_kind = models.CharField(max_length=24, choices=SnapshotKind.choices, default=SnapshotKind.OTHER)
    timeframe = models.CharField(max_length=32, blank=True, default="")
    as_of = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    summary_text = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    source_keys = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["snapshot_key"]),
            models.Index(fields=["workspace", "snapshot_kind"]),
            models.Index(fields=["ticker", "snapshot_kind", "timeframe"]),
            models.Index(fields=["portfolio", "snapshot_kind"]),
            models.Index(fields=["watchlist", "snapshot_kind"]),
        ]

    def __str__(self) -> str:
        scope = self.snapshot_key if self.snapshot_key else self.snapshot_kind
        return f"{self.snapshot_kind} snapshot {scope}"


class SchwabOAuthCredential(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="schwab_credentials",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schwab_credentials",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    token_payload_encrypted = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    token_type = models.CharField(max_length=64, blank=True, default="")
    scope = models.CharField(max_length=255, blank=True, default="")
    primary_account_hash = models.CharField(max_length=255, blank=True, default="")
    account_hashes = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "expires_at"]),
            models.Index(fields=["workspace", "is_active"]),
            models.Index(fields=["owner", "is_active"]),
        ]

    def __str__(self) -> str:
        scope = self.workspace_id or self.owner_id or self.id
        return f"Schwab credential {scope}"