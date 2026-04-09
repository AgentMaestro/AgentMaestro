from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from finance.providers.schwab import SchwabMarketDataProvider


class Command(BaseCommand):
    help = "Test the Schwab market-data instruments endpoint for a symbol."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--symbol", default="AAPL", help="Ticker symbol to inspect.")
        parser.add_argument(
            "--projection",
            default="fundamental",
            help="Schwab projection to request, e.g. fundamental.",
        )

    def handle(self, *args, **options):
        symbol = str(options.get("symbol") or "AAPL").strip().upper()
        projection = str(options.get("projection") or "fundamental").strip() or "fundamental"
        provider = SchwabMarketDataProvider()
        payload = provider.get_instrument(symbol, projection=projection)
        self.stdout.write(json.dumps(payload, indent=2, default=str))
