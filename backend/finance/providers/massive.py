from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import datetime
from datetime import date as date_class
from datetime import timedelta
from typing import Any

import httpx
from django.conf import settings

from .base import MarketDataProvider


class MassiveMarketDataProvider(MarketDataProvider):
    provider_name = "massive"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "MASSIVE_API_BASE_URL", "https://api.massive.com")).rstrip("/")
        self.api_key = api_key if api_key is not None else getattr(settings, "MASSIVE_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def _client(self) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "AgentMaestro/finance",
        }
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds, headers=headers) as client:
            yield client

    @staticmethod
    def _normalize_date_value(value: datetime | date_class | None) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.date().isoformat()
        return value.isoformat()

    @staticmethod
    def _coalesce_results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("results", "tickers", "ticker", "news", "contracts", "bars"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
        return []

    @staticmethod
    def _first_dict(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key in ("results", "ticker", "contract", "news", "bar"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return {}

    def _request_json(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_params = dict(params or {})
        if self.api_key:
            request_params["apiKey"] = self.api_key
        with self._client() as client:
            response = client.request(method, path, params=request_params)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _normalize_bar(bar: dict[str, Any]) -> dict[str, Any]:
        timestamp = bar.get("t") or bar.get("timestamp") or bar.get("participant_timestamp")
        return {
            "timestamp": timestamp,
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
            "vwap": bar.get("vw"),
            "transactions": bar.get("n"),
        }

    def _normalize_snapshot(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        ticker_payload = self._first_dict(payload)
        last_quote = ticker_payload.get("lastQuote") or ticker_payload.get("last_quote") or {}
        last_trade = ticker_payload.get("lastTrade") or ticker_payload.get("last_trade") or {}
        day = ticker_payload.get("day") or {}
        prev_day = ticker_payload.get("prevDay") or ticker_payload.get("prev_day") or {}
        min_snapshot = ticker_payload.get("min") or {}

        def first_positive_number(*values: Any) -> Any:
            for value in values:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if numeric > 0:
                    return numeric
            return None

        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "request_id": payload.get("request_id") or payload.get("requestId") or "",
            "status": payload.get("status") or "ok",
            "as_of": datetime.utcnow().isoformat() + "Z",
            "ticker": ticker_payload.get("ticker") or symbol,
            "snapshot": ticker_payload,
            "day": day,
            "prev_day": prev_day,
            "last_quote": last_quote,
            "last_trade": last_trade,
            "quote": {
                "bid": last_quote.get("bid") or last_quote.get("bp"),
                "ask": last_quote.get("ask") or last_quote.get("ap"),
                "bid_size": last_quote.get("bid_size") or last_quote.get("bs"),
                "ask_size": last_quote.get("ask_size") or last_quote.get("as"),
                "last": first_positive_number(
                    last_trade.get("price"),
                    last_trade.get("p"),
                    min_snapshot.get("c"),
                    day.get("c"),
                    prev_day.get("c"),
                    ticker_payload.get("last"),
                    ticker_payload.get("close"),
                ),
                "volume": day.get("volume") or last_trade.get("size"),
                "updated": ticker_payload.get("updated") or last_quote.get("t") or last_trade.get("t"),
            },
        }

    def get_quote(self, symbol: str) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        payload = self._request_json("GET", f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
        return self._normalize_snapshot(symbol, payload)

    def get_history(self, symbol: str, *, timeframe: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        normalized_timeframe = self._normalize_timeframe(timeframe)
        if normalized_timeframe in {"weekly", "1w", "week"}:
            multiplier, timespan = 1, "week"
        elif normalized_timeframe in {"monthly", "1mo", "month"}:
            multiplier, timespan = 1, "month"
        elif normalized_timeframe in {"hourly", "1h", "hour"}:
            multiplier, timespan = 1, "hour"
        else:
            multiplier, timespan = 1, "day"

        end_value = self._normalize_date_value(end or datetime.utcnow())
        if start is None:
            if timespan == "week":
                start = datetime.utcnow() - timedelta(days=365 * 2)
            elif timespan == "month":
                start = datetime.utcnow() - timedelta(days=365 * 5)
            elif timespan == "hour":
                start = datetime.utcnow() - timedelta(days=30)
            else:
                start = datetime.utcnow() - timedelta(days=365)
        start_value = self._normalize_date_value(start)
        payload = self._request_json(
            "GET",
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start_value}/{end_value}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000},
        )
        bars = [self._normalize_bar(item) for item in self._coalesce_results(payload)]
        previous_close = None
        previous_close_date = None
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "timeframe": normalized_timeframe or timespan,
            "start": start_value,
            "end": end_value,
            "request_id": payload.get("request_id") or "",
            "status": payload.get("status") or "ok",
            "bars": bars,
            "candles": [
                {
                    "timestamp": bar.get("timestamp"),
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": bar.get("volume"),
                }
                for bar in bars
            ],
            "previous_close": previous_close,
            "previous_close_date": previous_close_date,
            "count": len(bars),
        }

    def get_news(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        payload = self._request_json(
            "GET",
            "/v2/reference/news",
            params={
                "ticker": symbol,
                "limit": max(1, min(int(limit or 10), 50)),
                "sort": "published_utc",
                "order": "desc",
            },
        )
        items = []
        for item in self._coalesce_results(payload):
            items.append(
                {
                    "id": item.get("id") or item.get("article_id") or "",
                    "title": item.get("title") or "",
                    "publisher": item.get("publisher") or item.get("source") or "",
                    "published_utc": item.get("published_utc") or item.get("published") or "",
                    "article_url": item.get("article_url") or item.get("url") or "",
                    "amp_url": item.get("amp_url") or "",
                    "image_url": item.get("image_url") or "",
                    "description": item.get("description") or item.get("summary") or "",
                    "tickers": item.get("tickers") or [],
                    "keywords": item.get("keywords") or [],
                }
            )
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "request_id": payload.get("request_id") or "",
            "status": payload.get("status") or "ok",
            "count": len(items),
            "news": items,
        }

    def get_options_chain(self, symbol: str, *, expiration: str | None = None) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        params: dict[str, Any] = {
            "underlying_ticker": symbol,
            "limit": 1000,
        }
        if expiration:
            params["expiration_date"] = expiration
        payload = self._request_json("GET", "/v3/reference/options/contracts", params=params)
        contracts = self._coalesce_results(payload)
        normalized_contracts = [
            {
                "ticker": item.get("ticker") or item.get("options_ticker") or "",
                "underlying_ticker": item.get("underlying_ticker") or symbol,
                "contract_type": item.get("contract_type") or "",
                "exercise_style": item.get("exercise_style") or "",
                "expiration_date": item.get("expiration_date") or "",
                "strike_price": item.get("strike_price"),
                "shares_per_contract": item.get("shares_per_contract"),
                "primary_exchange": item.get("primary_exchange") or "",
                "additional_underlyings": item.get("additional_underlyings") or [],
            }
            for item in contracts
        ]
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "expiration": expiration or "",
            "request_id": payload.get("request_id") or "",
            "status": payload.get("status") or "ok",
            "count": len(normalized_contracts),
            "contracts": normalized_contracts,
        }

    def get_option_quote(self, contract_symbol: str) -> dict[str, Any]:
        contract_symbol = self._normalize_symbol(contract_symbol)
        try:
            payload = self._request_json("GET", f"/v3/snapshot/options/{contract_symbol}")
        except httpx.HTTPStatusError:
            payload = self._request_json("GET", f"/v3/reference/options/contracts/{contract_symbol}")
        snapshot = self._first_dict(payload)
        return {
            "provider": self.provider_name,
            "contract_symbol": contract_symbol,
            "request_id": payload.get("request_id") or "",
            "status": payload.get("status") or "ok",
            "snapshot": snapshot,
            "quote": {
                "bid": snapshot.get("bid_price") or snapshot.get("bid") or snapshot.get("bp"),
                "ask": snapshot.get("ask_price") or snapshot.get("ask") or snapshot.get("ap"),
                "bid_size": snapshot.get("bid_size") or snapshot.get("bs"),
                "ask_size": snapshot.get("ask_size") or snapshot.get("as"),
                "last": snapshot.get("last_price") or snapshot.get("price") or snapshot.get("trade_price"),
                "updated": snapshot.get("updated") or snapshot.get("timestamp") or snapshot.get("t"),
            },
        }

    def get_option_greeks(self, contract_symbol: str) -> dict[str, Any]:
        contract_symbol = self._normalize_symbol(contract_symbol)
        payload = self.get_option_quote(contract_symbol)
        snapshot = payload.get("snapshot") or {}
        greeks = snapshot.get("greeks") or {}
        return {
            "provider": self.provider_name,
            "contract_symbol": contract_symbol,
            "request_id": payload.get("request_id") or "",
            "status": payload.get("status") or "ok",
            "greeks": greeks,
            "implied_volatility": snapshot.get("implied_volatility") or snapshot.get("iv"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "rho": greeks.get("rho"),
        }

    def price_option_black_scholes(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        option_kind = option_type.strip().lower()
        if option_kind not in {"call", "put"}:
            raise ValueError("option_type must be 'call' or 'put'.")
        if time_to_expiry_years <= 0:
            intrinsic = max(0.0, spot - strike) if option_kind == "call" else max(0.0, strike - spot)
            return {
                "provider": self.provider_name,
                "symbol": symbol,
                "model": "black_scholes",
                "option_type": option_kind,
                "price": intrinsic,
                "inputs": {
                    "strike": strike,
                    "spot": spot,
                    "rate": rate,
                    "volatility": volatility,
                    "time_to_expiry_years": time_to_expiry_years,
                },
            }
        if volatility <= 0:
            intrinsic = max(0.0, spot - strike) if option_kind == "call" else max(0.0, strike - spot)
            return {
                "provider": self.provider_name,
                "symbol": symbol,
                "model": "black_scholes",
                "option_type": option_kind,
                "price": intrinsic,
                "inputs": {
                    "strike": strike,
                    "spot": spot,
                    "rate": rate,
                    "volatility": volatility,
                    "time_to_expiry_years": time_to_expiry_years,
                },
            }

        sqrt_t = math.sqrt(time_to_expiry_years)
        d1 = (math.log(spot / strike) + (rate + 0.5 * volatility**2) * time_to_expiry_years) / (volatility * sqrt_t)
        d2 = d1 - volatility * sqrt_t
        nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
        nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
        n_minus_d1 = 1.0 - nd1
        n_minus_d2 = 1.0 - nd2
        discount = math.exp(-rate * time_to_expiry_years)
        if option_kind == "call":
            price = spot * nd1 - strike * discount * nd2
        else:
            price = strike * discount * n_minus_d2 - spot * n_minus_d1
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "model": "black_scholes",
            "option_type": option_kind,
            "price": price,
            "d1": d1,
            "d2": d2,
            "inputs": {
                "strike": strike,
                "spot": spot,
                "rate": rate,
                "volatility": volatility,
                "time_to_expiry_years": time_to_expiry_years,
            },
        }

    def price_option_binomial(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str, steps: int = 100) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        option_kind = option_type.strip().lower()
        if option_kind not in {"call", "put"}:
            raise ValueError("option_type must be 'call' or 'put'.")
        steps = max(1, int(steps or 100))
        if time_to_expiry_years <= 0:
            intrinsic = max(0.0, spot - strike) if option_kind == "call" else max(0.0, strike - spot)
            return {
                "provider": self.provider_name,
                "symbol": symbol,
                "model": "binomial",
                "option_type": option_kind,
                "price": intrinsic,
                "steps": steps,
                "inputs": {
                    "strike": strike,
                    "spot": spot,
                    "rate": rate,
                    "volatility": volatility,
                    "time_to_expiry_years": time_to_expiry_years,
                },
            }

        dt = time_to_expiry_years / steps
        u = math.exp(volatility * math.sqrt(dt))
        d = 1.0 / u
        discount = math.exp(-rate * dt)
        growth = math.exp(rate * dt)
        p = (growth - d) / (u - d)
        p = max(0.0, min(1.0, p))

        values: list[float] = []
        for i in range(steps + 1):
            price_at_node = spot * (u ** (steps - i)) * (d ** i)
            payoff = max(0.0, price_at_node - strike) if option_kind == "call" else max(0.0, strike - price_at_node)
            values.append(payoff)

        for step in range(steps - 1, -1, -1):
            next_values: list[float] = []
            for i in range(step + 1):
                continuation = discount * (p * values[i] + (1.0 - p) * values[i + 1])
                price_at_node = spot * (u ** (step - i)) * (d ** i)
                exercise = max(0.0, price_at_node - strike) if option_kind == "call" else max(0.0, strike - price_at_node)
                next_values.append(max(continuation, exercise))
            values = next_values

        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "model": "binomial",
            "option_type": option_kind,
            "price": values[0],
            "steps": steps,
            "inputs": {
                "strike": strike,
                "spot": spot,
                "rate": rate,
                "volatility": volatility,
                "time_to_expiry_years": time_to_expiry_years,
            },
        }
