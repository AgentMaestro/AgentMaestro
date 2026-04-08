from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import httpx
from django.conf import settings

from .base import FilingsProvider


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"


def _sec_user_agent() -> str:
    slug = str(getattr(settings, "FINANCE_AGENT_SLUG", "") or "").strip()
    if slug:
        return f"AgentMaestro/finance/{slug} support@agentmaestro.local"
    return "AgentMaestro/finance support@agentmaestro.local"


@lru_cache(maxsize=1)
def _company_ticker_map(user_agent: str) -> dict[str, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    with httpx.Client(timeout=30.0, headers=headers) as client:
        response = client.get(SEC_COMPANY_TICKERS_URL)
        response.raise_for_status()
        payload = response.json()
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if payload and all(isinstance(value, dict) for value in payload.values()):
            rows = [value for value in payload.values() if isinstance(value, dict)]
        elif isinstance(payload.get("data"), list):
            rows = [item for item in payload["data"] if isinstance(item, dict)]
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        if symbol:
            mapping[symbol] = row
    return mapping


class SECFilingsProvider(FilingsProvider):
    provider_name = "sec"

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def _client(self) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": _sec_user_agent(),
        }
        with httpx.Client(timeout=self.timeout_seconds, headers=headers) as client:
            yield client

    def _request_json(self, url: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_filings(self, symbol: str, *, filing_types: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        ticker = self._normalize_symbol(symbol)
        if not ticker:
            return {
                "provider": self.provider_name,
                "status": "invalid",
                "message": "symbol is required",
                "symbol": "",
                "count": 0,
                "filings": [],
            }

        user_agent = _sec_user_agent()
        company_map = _company_ticker_map(user_agent)
        company = company_map.get(ticker)
        if company is None:
            return {
                "provider": self.provider_name,
                "status": "not_found",
                "message": f"No SEC company_tickers match for {ticker}.",
                "symbol": ticker,
                "count": 0,
                "filings": [],
            }

        cik_raw = str(company.get("cik_str") or company.get("cik") or "").strip()
        cik = cik_raw.zfill(10)
        submissions_url = f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json"
        payload = self._request_json(submissions_url)
        filings = payload.get("filings") if isinstance(payload.get("filings"), dict) else {}
        recent = filings.get("recent") if isinstance(filings, dict) else {}
        forms = list(recent.get("form") or [])
        filing_dates = list(recent.get("filingDate") or [])
        report_dates = list(recent.get("reportDate") or [])
        accession_numbers = list(recent.get("accessionNumber") or [])
        primary_documents = list(recent.get("primaryDocument") or [])
        descriptions = list(recent.get("primaryDocDescription") or [])
        permitted_forms = {str(item).strip().upper() for item in (filing_types or []) if str(item).strip()}
        max_results = max(1, min(int(limit or 20), 50))
        company_name = str(company.get("title") or company.get("name") or "").strip()
        filing_rows: list[dict[str, Any]] = []
        for idx, form in enumerate(forms):
            form_name = str(form or "").strip().upper()
            if permitted_forms and form_name not in permitted_forms:
                continue
            accession = accession_numbers[idx] if idx < len(accession_numbers) else ""
            primary_document = primary_documents[idx] if idx < len(primary_documents) else ""
            accession_clean = str(accession or "").replace("-", "")
            filing_url = ""
            if accession_clean and primary_document:
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{primary_document}"
            filing_rows.append(
                {
                    "form": form_name,
                    "filing_date": filing_dates[idx] if idx < len(filing_dates) else "",
                    "report_date": report_dates[idx] if idx < len(report_dates) else "",
                    "accession_number": accession,
                    "primary_document": primary_document,
                    "description": descriptions[idx] if idx < len(descriptions) else "",
                    "filing_url": filing_url,
                }
            )
            if len(filing_rows) >= max_results:
                break

        if not filing_rows:
            return {
                "provider": self.provider_name,
                "status": "empty",
                "symbol": ticker,
                "company_name": company_name,
                "cik": cik,
                "count": 0,
                "filings": [],
                "message": "No filings matched the requested filing types.",
            }

        return {
            "provider": self.provider_name,
            "status": "ok",
            "symbol": ticker,
            "company_name": company_name,
            "cik": cik,
            "exchange": str(company.get("exchange") or "").strip(),
            "count": len(filing_rows),
            "filing_types": [str(item) for item in filing_types or []],
            "filings": filing_rows,
        }
