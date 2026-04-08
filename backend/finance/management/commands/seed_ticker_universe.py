from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from finance.services.ticker_universe import refresh_ticker_universe


class Command(BaseCommand):
    help = "Seed or refresh the Massive-backed ticker universe cache."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--page-size",
            type=int,
            default=None,
            help="Number of tickers to request per Massive page. Defaults to FINANCE_TICKER_UNIVERSE_PAGE_SIZE.",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=None,
            help="Maximum number of pages to fetch. Defaults to FINANCE_TICKER_UNIVERSE_MAX_PAGES.",
        )

    def handle(self, *args, **options):
        page_size = options.get("page_size")
        max_pages = options.get("max_pages")
        result = refresh_ticker_universe(max_pages=max_pages, page_size=page_size)
        self.stdout.write(
            self.style.SUCCESS(
                "Ticker universe refresh complete: "
                f"provider={result.get('provider')} "
                f"markets={','.join(result.get('ticker_markets') or [])} "
                f"types={','.join(result.get('ticker_types') or [])} "
                f"pages={result.get('pages')} "
                f"rows={result.get('rows')} "
                f"upserted={result.get('upserted')} "
                f"finished={bool(result.get('finished'))} "
                f"as_of={result.get('as_of')}"
            )
        )
        self.stdout.write(
            f"Defaults: page_size={page_size or getattr(settings, 'FINANCE_TICKER_UNIVERSE_PAGE_SIZE', 1000)} "
            f"max_pages={max_pages or getattr(settings, 'FINANCE_TICKER_UNIVERSE_MAX_PAGES', 50)}"
        )
        return None
