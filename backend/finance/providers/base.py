from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class FinanceProviderError(RuntimeError):
    pass


class FinanceProvider(ABC):
    provider_name: str = ""
    provider_kind: str = ""

    def __str__(self) -> str:
        return f"{self.provider_kind}:{self.provider_name}"

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return symbol.strip().upper()

    @staticmethod
    def _normalize_timeframe(timeframe: str | None) -> str:
        return (timeframe or "").strip().lower()


class MarketDataProvider(FinanceProvider):
    provider_kind = "market_data"

    @abstractmethod
    def get_quote(self, symbol: str, *, fields: str | list[str] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def get_quotes(self, symbols: list[str], *, fields: str | list[str] | None = None) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            candidate = self._normalize_symbol(symbol)
            if not candidate or candidate in normalized:
                continue
            normalized[candidate] = self.get_quote(candidate, fields=fields)
        return normalized

    @abstractmethod
    def get_history(self, symbol: str, *, timeframe: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def get_instrument(self, symbol: str, *, projection: str = "fundamental") -> dict[str, Any]:
        raise NotImplementedError

    def get_market_hours(self, markets: list[str], *, date=None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_news(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_options_chain(self, symbol: str, *, expiration: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_option_quote(self, contract_symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_option_greeks(self, contract_symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def price_option_black_scholes(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def price_option_binomial(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str, steps: int = 100) -> dict[str, Any]:
        raise NotImplementedError


class FilingsProvider(FinanceProvider):
    provider_kind = "filings"

    @abstractmethod
    def get_filings(self, symbol: str, *, filing_types: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        raise NotImplementedError


class BrokerageProvider(FinanceProvider):
    provider_kind = "brokerage"

    @abstractmethod
    def list_accounts(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_balances(self, account_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_positions(self, account_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_activity(self, account_id: str | None = None, *, limit: int = 50) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def preview_order(self, *, account_id: str, symbol: str, side: str, quantity: float, order_type: str, time_in_force: str, limit_price: float | None = None, stop_price: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, *, account_id: str, symbol: str, side: str, quantity: float, order_type: str, time_in_force: str, limit_price: float | None = None, stop_price: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, *, account_id: str, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, *, account_id: str, order_id: str) -> dict[str, Any]:
        raise NotImplementedError
