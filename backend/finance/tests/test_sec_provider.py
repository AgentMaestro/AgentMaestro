from __future__ import annotations

import httpx

from finance.providers import sec as sec_provider_module
from finance.providers.sec import SECFilingsProvider


class _FakeClient:
    def __init__(self, *, timeout: float, headers: dict[str, str]) -> None:
        self.timeout = timeout
        self.headers = headers

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url == "https://www.sec.gov/files/company_tickers.json":
            payload = {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc.", "exchange": "NASDAQ"},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation", "exchange": "NASDAQ"},
            }
            return httpx.Response(200, json=payload, request=request)
        if url == "https://data.sec.gov/submissions/CIK0000320193.json":
            payload = {
                "name": "Apple Inc.",
                "filings": {
                    "recent": {
                        "form": ["10-Q", "8-K", "10-K"],
                        "filingDate": ["2026-04-01", "2026-03-15", "2025-11-01"],
                        "reportDate": ["2026-03-31", "2026-03-15", "2025-09-30"],
                        "accessionNumber": ["0000320193-26-000010", "0000320193-26-000011", "0000320193-25-000020"],
                        "primaryDocument": ["aapl-20260331x10q.htm", "aapl-20260315x8k.htm", "aapl-20250930x10k.htm"],
                        "primaryDocDescription": ["Quarterly report", "Current report", "Annual report"],
                    }
                },
            }
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError(f"Unexpected SEC url: {url}")


def test_sec_filings_provider_returns_recent_filings(monkeypatch):
    sec_provider_module._company_ticker_map.cache_clear()
    monkeypatch.setattr(sec_provider_module.settings, "FINANCE_AGENT_SLUG", "jeeves", raising=False)
    captured_headers: dict[str, str] = {}

    def _client_factory(*args, **kwargs):
        captured_headers.clear()
        captured_headers.update(kwargs.get("headers", {}))
        return _FakeClient(timeout=kwargs.get("timeout", 30.0), headers=kwargs.get("headers", {}))

    monkeypatch.setattr("finance.providers.sec.httpx.Client", _client_factory)
    provider = SECFilingsProvider()

    result = provider.get_filings("AAPL", filing_types=["10-Q", "8-K"], limit=5)

    assert result["provider"] == "sec"
    assert result["status"] == "ok"
    assert result["symbol"] == "AAPL"
    assert result["company_name"] == "Apple Inc."
    assert result["count"] == 2
    assert result["filings"][0]["form"] == "10-Q"
    assert result["filings"][0]["filing_url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert "jeeves" in sec_provider_module._sec_user_agent()
    assert "jeeves" in captured_headers.get("User-Agent", "")
