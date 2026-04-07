from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0002_useractionlog"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Ticker",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("symbol", models.CharField(max_length=24, unique=True)),
                ("name", models.CharField(blank=True, default="", max_length=180)),
                ("exchange", models.CharField(blank=True, default="", max_length=64)),
                (
                    "asset_type",
                    models.CharField(
                        choices=[
                            ("EQUITY", "Equity"),
                            ("ETF", "ETF"),
                            ("INDEX", "Index"),
                            ("OPTION", "Option"),
                            ("FUND", "Fund"),
                            ("CRYPTO", "Crypto"),
                            ("OTHER", "Other"),
                        ],
                        default="EQUITY",
                        max_length=16,
                    ),
                ),
                ("currency", models.CharField(default="USD", max_length=8)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.CreateModel(
            name="Portfolio",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("base_currency", models.CharField(default="USD", max_length=8)),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_portfolios",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_portfolios",
                        to="core.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Watchlist",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_watchlists",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_watchlists",
                        to="core.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Position",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "side",
                    models.CharField(
                        choices=[("LONG", "Long"), ("SHORT", "Short")],
                        default="LONG",
                        max_length=8,
                    ),
                ),
                ("quantity", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("average_cost", models.DecimalField(decimal_places=4, default=0, max_digits=24)),
                ("cost_basis", models.DecimalField(decimal_places=2, default=0, max_digits=24)),
                ("notes", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "portfolio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="positions",
                        to="finance.portfolio",
                    ),
                ),
                (
                    "ticker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="positions",
                        to="finance.ticker",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="FinanceDataCacheEntry",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cache_key", models.CharField(max_length=255, unique=True)),
                (
                    "data_kind",
                    models.CharField(
                        choices=[
                            ("QUOTE", "Quote"),
                            ("HISTORY", "History"),
                            ("NEWS", "News"),
                            ("FILINGS", "Filings"),
                            ("OPTIONS_CHAIN", "Options Chain"),
                            ("INDICATOR", "Indicator"),
                            ("RESEARCH", "Research"),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        max_length=24,
                    ),
                ),
                ("source_name", models.CharField(blank=True, default="", max_length=64)),
                ("timeframe", models.CharField(blank=True, default="", max_length=32)),
                ("as_of", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("summary_text", models.TextField(blank=True, default="")),
                ("response_hash", models.CharField(blank=True, default="", max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "portfolio",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cache_entries",
                        to="finance.portfolio",
                    ),
                ),
                (
                    "ticker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cache_entries",
                        to="finance.ticker",
                    ),
                ),
                (
                    "watchlist",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cache_entries",
                        to="finance.watchlist",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_cache_entries",
                        to="core.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="FinanceResearchSnapshot",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("snapshot_key", models.CharField(max_length=255, unique=True)),
                (
                    "snapshot_kind",
                    models.CharField(
                        choices=[
                            ("PORTFOLIO", "Portfolio"),
                            ("WATCHLIST", "Watchlist"),
                            ("TICKER", "Ticker"),
                            ("COMPARISON", "Comparison"),
                            ("SCREEN", "Screen"),
                            ("OTHER", "Other"),
                        ],
                        default="OTHER",
                        max_length=24,
                    ),
                ),
                ("timeframe", models.CharField(blank=True, default="", max_length=32)),
                ("as_of", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("summary_text", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("source_keys", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "portfolio",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="snapshots",
                        to="finance.portfolio",
                    ),
                ),
                (
                    "ticker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="snapshots",
                        to="finance.ticker",
                    ),
                ),
                (
                    "watchlist",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="snapshots",
                        to="finance.watchlist",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="finance_snapshots",
                        to="core.workspace",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="portfolio",
            constraint=models.UniqueConstraint(
                fields=("workspace", "name"),
                name="finance_portfolio_workspace_name_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="watchlist",
            constraint=models.UniqueConstraint(
                fields=("workspace", "name"),
                name="finance_watchlist_workspace_name_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="position",
            constraint=models.UniqueConstraint(
                fields=("portfolio", "ticker", "side"),
                name="finance_position_portfolio_ticker_side_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="ticker",
            index=models.Index(fields=["symbol"], name="finance_ticker_symbol_idx"),
        ),
        migrations.AddIndex(
            model_name="ticker",
            index=models.Index(fields=["exchange", "asset_type"], name="finance_ticker_exchange_asset_idx"),
        ),
        migrations.AddIndex(
            model_name="portfolio",
            index=models.Index(fields=["workspace", "is_default"], name="finance_portfolio_workspace_default_idx"),
        ),
        migrations.AddIndex(
            model_name="portfolio",
            index=models.Index(fields=["owner", "created_at"], name="finance_portfolio_owner_created_idx"),
        ),
        migrations.AddIndex(
            model_name="watchlist",
            index=models.Index(fields=["workspace", "is_default"], name="finance_watchlist_workspace_default_idx"),
        ),
        migrations.AddIndex(
            model_name="watchlist",
            index=models.Index(fields=["owner", "created_at"], name="finance_watchlist_owner_created_idx"),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["portfolio", "ticker"], name="finance_position_portfolio_ticker_idx"),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["ticker", "side"], name="finance_position_ticker_side_idx"),
        ),
        migrations.AddIndex(
            model_name="financedatacacheentry",
            index=models.Index(fields=["cache_key"], name="finance_cache_cache_key_idx"),
        ),
        migrations.AddIndex(
            model_name="financedatacacheentry",
            index=models.Index(fields=["data_kind", "expires_at"], name="finance_cache_kind_expires_idx"),
        ),
        migrations.AddIndex(
            model_name="financedatacacheentry",
            index=models.Index(fields=["ticker", "data_kind", "timeframe"], name="finance_cache_ticker_kind_tf_idx"),
        ),
        migrations.AddIndex(
            model_name="financedatacacheentry",
            index=models.Index(fields=["workspace", "data_kind"], name="finance_cache_workspace_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="financeresearchsnapshot",
            index=models.Index(fields=["snapshot_key"], name="finance_snapshot_key_idx"),
        ),
        migrations.AddIndex(
            model_name="financeresearchsnapshot",
            index=models.Index(fields=["workspace", "snapshot_kind"], name="finance_snapshot_workspace_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="financeresearchsnapshot",
            index=models.Index(fields=["ticker", "snapshot_kind", "timeframe"], name="finance_snapshot_ticker_kind_tf_idx"),
        ),
        migrations.AddIndex(
            model_name="financeresearchsnapshot",
            index=models.Index(fields=["portfolio", "snapshot_kind"], name="finance_snapshot_portfolio_kind_idx"),
        ),
        migrations.AddIndex(
            model_name="financeresearchsnapshot",
            index=models.Index(fields=["watchlist", "snapshot_kind"], name="finance_snapshot_watchlist_kind_idx"),
        ),
    ]
