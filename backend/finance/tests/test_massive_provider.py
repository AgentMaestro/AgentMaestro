from __future__ import annotations

from datetime import date

import httpx

from finance.providers.massive import MassiveMarketDataProvider


class _FakeClient:
    def __init__(self, *, base_url: str, timeout: float, headers: dict[str, str]) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.headers = headers
        self.requests: list[tuple[str, str, dict[str, str] | None]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def request(self, method: str, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        self.requests.append((method, path, params))
        url = f"{self.base_url}{path}"
        request = httpx.Request(method, url)
        if path.startswith("/v2/snapshot/locale/us/markets/stocks/tickers/AAPL"):
            payload = {
                "request_id": "req-1",
                "results": {
                    "ticker": "AAPL",
                    "day": {"c": 195.5, "volume": 1000},
                    "lastQuote": {"bid": 195.4, "ask": 195.6, "bid_size": 10, "ask_size": 12, "t": 1},
                    "lastTrade": {"price": 195.5, "size": 5, "t": 2},
                },
            }
            return httpx.Response(200, json=payload, request=request)
        if path.startswith("/v2/aggs/ticker/AAPL/range/1/day/2026-01-01/2026-01-31"):
            payload = {"request_id": "req-2", "results": [{"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "vw": 1.2, "n": 3}]}
            return httpx.Response(200, json=payload, request=request)
        if path.startswith("/v2/reference/news"):
            payload = {
                "request_id": "req-3",
                "results": [
                    {
                        "id": "n1",
                        "title": "AAPL news",
                        "publisher": "Massive",
                        "published_utc": "2026-03-31T00:00:00Z",
                        "article_url": "https://example.com/aapl",
                        "keywords": ["AAPL"],
                    }
                ],
            }
            return httpx.Response(200, json=payload, request=request)
        if path.startswith("/v3/reference/options/contracts"):
            payload = {
                "request_id": "req-4",
                "results": [
                    {
                        "ticker": "AAPL260417C00100000",
                        "underlying_ticker": "AAPL",
                        "contract_type": "call",
                        "expiration_date": "2026-04-17",
                        "strike_price": 100,
                    }
                ],
            }
            return httpx.Response(200, json=payload, request=request)
        if path.startswith("/v3/snapshot/options/AAPL260417C00100000"):
            payload = {
                "request_id": "req-5",
                "results": {
                    "ticker": "AAPL260417C00100000",
                    "bid_price": 1.2,
                    "ask_price": 1.3,
                    "greeks": {"delta": 0.5, "gamma": 0.1, "theta": -0.02, "vega": 0.11, "rho": 0.04},
                },
            }
            return httpx.Response(200, json=payload, request=request)
        if path.startswith("/v3/reference/options/contracts/AAPL260417C00100000"):
            payload = {"request_id": "req-6", "results": {"ticker": "AAPL260417C00100000", "strike_price": 100}}
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError(f"Unexpected Massive path: {path}")


def test_massive_provider_shaping_and_pricing(monkeypatch):
    client = _FakeClient(base_url="https://api.massive.com", timeout=20.0, headers={})

    def _client_factory(*args, **kwargs):
        client.base_url = kwargs.get("base_url", client.base_url)
        client.timeout = kwargs.get("timeout", client.timeout)
        client.headers = kwargs.get("headers", client.headers)
        return client

    monkeypatch.setattr("finance.providers.massive.httpx.Client", _client_factory)
    provider = MassiveMarketDataProvider(base_url="https://api.massive.com", api_key="test-key")

    quote = provider.get_quote("AAPL")
    history = provider.get_history("AAPL", timeframe="daily", start=date(2026, 1, 1), end=date(2026, 1, 31))
    news = provider.get_news("AAPL", limit=1)
    chain = provider.get_options_chain("AAPL", expiration="2026-04-17")
    option_quote = provider.get_option_quote("AAPL260417C00100000")
    greeks = provider.get_option_greeks("AAPL260417C00100000")
    bs = provider.price_option_black_scholes(
        symbol="AAPL",
        strike=100.0,
        spot=110.0,
        rate=0.05,
        volatility=0.2,
        time_to_expiry_years=1.0,
        option_type="call",
    )
    binomial = provider.price_option_binomial(
        symbol="AAPL",
        strike=100.0,
        spot=110.0,
        rate=0.05,
        volatility=0.2,
        time_to_expiry_years=1.0,
        option_type="call",
        steps=10,
    )

    assert quote["symbol"] == "AAPL"
    assert quote["quote"]["bid"] == 195.4
    assert history["count"] == 1
    assert history["bars"][0]["close"] == 1.5
    assert news["count"] == 1
    assert news["news"][0]["title"] == "AAPL news"
    assert chain["count"] == 1
    assert chain["contracts"][0]["ticker"] == "AAPL260417C00100000"
    assert option_quote["quote"]["ask"] == 1.3
    assert greeks["delta"] == 0.5
    assert bs["model"] == "black_scholes"
    assert bs["price"] > 0
    assert binomial["model"] == "binomial"
    assert binomial["price"] > 0
