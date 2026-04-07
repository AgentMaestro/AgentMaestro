from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from .base import BrokerageProvider, FilingsProvider, MarketDataProvider
from .massive import MassiveMarketDataProvider
from .schwab import SchwabBrokerageProvider, SchwabMarketDataProvider
from .sec import SECFilingsProvider


@dataclass(frozen=True, slots=True)
class FinanceProviderDescriptor:
    provider_kind: str
    provider_name: str
    label: str
    phase: str
    summary: str


def get_default_provider_catalog() -> list[FinanceProviderDescriptor]:
    return [
        FinanceProviderDescriptor(
            provider_kind="market_data",
            provider_name="schwab",
            label="Schwab",
            phase="phase_1",
            summary="Primary source for real-time quote data.",
        ),
        FinanceProviderDescriptor(
            provider_kind="market_data_backup",
            provider_name="massive",
            label="Massive",
            phase="phase_1_and_phase_2",
            summary="Fallback source for stock history, news, and other non-real-time market data.",
        ),
        FinanceProviderDescriptor(
            provider_kind="filings",
            provider_name="sec",
            label="SEC EDGAR",
            phase="phase_1",
            summary="Authoritative source for corporate filings.",
        ),
        FinanceProviderDescriptor(
            provider_kind="brokerage",
            provider_name="schwab",
            label="Schwab",
            phase="phase_1_and_phase_2",
            summary="Read-only portfolio adapter in phase 1 and approval-gated order placement in phase 2.",
        ),
    ]


def build_default_providers(*, workspace=None, owner=None) -> dict[str, MarketDataProvider | FilingsProvider | BrokerageProvider]:
    primary_market_data = (getattr(settings, "FINANCE_MARKET_DATA_PROVIDER", "schwab") or "schwab").strip().lower()
    backup_market_data = (getattr(settings, "FINANCE_MARKET_DATA_BACKUP_PROVIDER", "massive") or "massive").strip().lower()
    return {
        "market_data": SchwabMarketDataProvider() if primary_market_data == "schwab" else MassiveMarketDataProvider(),
        "market_data_backup": MassiveMarketDataProvider() if backup_market_data == "massive" else SchwabMarketDataProvider(),
        "filings": SECFilingsProvider(),
        "brokerage": SchwabBrokerageProvider(workspace_id=getattr(workspace, "id", None), owner_id=getattr(owner, "id", None)),
    }
