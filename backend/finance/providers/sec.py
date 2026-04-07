from __future__ import annotations

from typing import Any

from .base import FilingsProvider


class SECFilingsProvider(FilingsProvider):
    provider_name = "sec"

    def get_filings(self, symbol: str, *, filing_types: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        raise NotImplementedError("SEC filings adapter is not wired yet.")
