from __future__ import annotations

import pytest

from finance.models import TickerUniverseEntry
from finance.services.ticker_universe import search_ticker_universe


@pytest.mark.django_db
def test_search_ticker_universe_finds_symbol_and_name_matches():
    TickerUniverseEntry.objects.create(
        symbol="AAPL",
        name="Apple Inc.",
        exchange="XNAS",
        asset_type="EQUITY",
        currency="USD",
        is_active=True,
        source_name="massive",
        source_payload={"ticker": "AAPL"},
        metadata={"source": "test"},
    )

    by_symbol = search_ticker_universe("AAPL", limit=5)
    by_name = search_ticker_universe("Apple", limit=5)

    assert by_symbol
    assert by_symbol[0]["symbol"] == "AAPL"
    assert by_name
    assert by_name[0]["symbol"] == "AAPL"
