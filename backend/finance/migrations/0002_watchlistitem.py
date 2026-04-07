from __future__ import annotations

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WatchlistItem",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("note", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "added_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="finance_watchlist_items_added",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "ticker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="watchlist_items",
                        to="finance.ticker",
                    ),
                ),
                (
                    "watchlist",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="finance.watchlist",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["watchlist", "created_at"],
                        name="finance_watchlist_item_watchlist_created_idx",
                    ),
                    models.Index(
                        fields=["ticker", "created_at"],
                        name="finance_watchlist_item_ticker_created_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("watchlist", "ticker"),
                        name="finance_watchlist_item_watchlist_ticker_unique",
                    ),
                ],
            },
        ),
    ]
