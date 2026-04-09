from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from django.conf import settings
from django.utils import timezone

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, Ticker, TickerUniverseEntry
from finance.providers.registry import build_default_providers


logger = get_app_logger("finance")


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _truncate_text(value: object, max_length: int) -> str:
    text = _normalize_text(value)
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()


def _news_cache_key(symbol: str, source: str) -> str:
    return f"news:{_normalize_symbol(symbol)}:{_normalize_text(source).lower()}"


def _serialize_cache_entry(entry: FinanceDataCacheEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "cache_key": entry.cache_key,
        "data_kind": entry.data_kind,
        "timeframe": entry.timeframe,
        "as_of": entry.as_of.isoformat() if entry.as_of else "",
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else "",
        "payload": dict(entry.payload or {}),
        "summary_text": entry.summary_text,
        "metadata": dict(entry.metadata or {}),
    }


def _upsert_news_cache(workspace, ticker: Ticker, source: str, payload: dict[str, Any]) -> FinanceDataCacheEntry:
    now = timezone.now()
    ttl_seconds = int(getattr(settings, "FINANCE_NEWS_TTL_SECONDS", 3600))
    expires_at = now + timedelta(seconds=max(1, ttl_seconds))
    source_name = _normalize_text(payload.get("provider") or source or "news").lower() or "news"
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_news_cache_key(ticker.symbol, source_name),
        defaults={
            "workspace": workspace,
            "ticker": ticker,
            "data_kind": FinanceDataCacheEntry.DataKind.NEWS,
            "source_name": source_name,
            "timeframe": "",
            "as_of": now,
            "expires_at": expires_at,
            "payload": payload,
            "summary_text": f"{ticker.symbol} {source_name} news cached for finance research.",
            "response_hash": "",
            "metadata": {
                "bootstrap": False,
                "ttl_seconds": ttl_seconds,
                "source": source_name,
            },
        },
    )
    entry.refresh_from_db()
    logger.info(
        "finance news cache upserted symbol=%s source=%s as_of=%s expires_at=%s ttl_seconds=%s",
        ticker.symbol,
        source_name,
        entry.as_of.isoformat() if entry.as_of else "",
        entry.expires_at.isoformat() if entry.expires_at else "",
        ttl_seconds,
    )
    return entry


def _build_unavailable_payload(*, provider: str, symbol: str, query: str, message: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "symbol": symbol,
        "query": query,
        "status": "unavailable",
        "message": message,
        "news": [],
        "results": [],
    }


def _normalize_massive_news(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in payload.get("news") or payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id": item.get("id") or item.get("article_id") or item.get("news_id") or "",
                "title": item.get("title") or "",
                "publisher": item.get("publisher") or item.get("source") or "",
                "published_utc": item.get("published_utc") or item.get("published") or item.get("created_at") or "",
                "article_url": item.get("article_url") or item.get("url") or "",
                "image_url": item.get("image_url") or "",
                "description": item.get("description") or item.get("summary") or "",
                "tickers": item.get("tickers") or [symbol],
                "keywords": item.get("keywords") or [],
            }
        )
    return {
        "provider": payload.get("provider") or "massive",
        "symbol": symbol,
        "request_id": payload.get("request_id") or "",
        "status": payload.get("status") or ("ok" if items else "empty"),
        "count": len(items),
        "news": items,
    }


def _search_web_news(symbol: str, company_name: str, limit: int) -> dict[str, Any]:
    api_key = str(getattr(settings, "BRAVE_SEARCH_API_KEY", "") or "").strip()
    query = " ".join(part for part in [symbol, company_name, "news"] if part).strip()
    if not api_key:
        return _build_unavailable_payload(
            provider="web_search",
            symbol=symbol,
            query=query,
            message="BRAVE_SEARCH_API_KEY is not configured.",
        )

    timeout = float(getattr(settings, "FINANCE_NEWS_WEB_SEARCH_TIMEOUT_SECONDS", 10.0))
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": max(1, min(int(limit or 10), 10)),
                    "text_decorations": False,
                    "search_lang": "en",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        logger.warning("finance web news search timed out symbol=%s query=%r error=%s", symbol, query, exc)
        return _build_unavailable_payload(provider="web_search", symbol=symbol, query=query, message="web search timed out")
    except httpx.HTTPError as exc:
        logger.warning("finance web news search failed symbol=%s query=%r error=%s", symbol, query, exc)
        return _build_unavailable_payload(provider="web_search", symbol=symbol, query=query, message=str(exc))

    results = (((payload or {}).get("web") or {}).get("results") or [])[: max(1, min(int(limit or 10), 10))]
    items: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id": item.get("id") or item.get("url") or "",
                "title": item.get("title") or "",
                "publisher": item.get("source") or "web",
                "published_utc": item.get("age") or "",
                "article_url": item.get("url") or "",
                "image_url": "",
                "description": item.get("description") or item.get("snippet") or "",
                "tickers": [symbol],
                "keywords": [],
            }
        )
    if not items:
        logger.info("finance web news search returned no results symbol=%s query=%r", symbol, query)
    return {
        "provider": "web_search",
        "symbol": symbol,
        "query": query,
        "status": "ok" if items else "empty",
        "count": len(items),
        "news": items,
        "results": items,
        "raw": payload,
    }


def refresh_ticker_news(*, workspace, owner, symbol: str, limit: int = 10) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    if not symbol:
        return {"refreshed": False, "symbol": "", "status": "empty"}

    lookup_entry = TickerUniverseEntry.objects.filter(symbol=symbol).first()
    ticker = Ticker.objects.filter(symbol=symbol).first()
    if ticker is None:
        defaults = {
            "name": getattr(lookup_entry, "name", ""),
            "exchange": getattr(lookup_entry, "exchange", ""),
            "asset_type": getattr(lookup_entry, "asset_type", Ticker.AssetType.EQUITY),
            "currency": getattr(lookup_entry, "currency", "USD") or "USD",
            "is_active": True if lookup_entry is None else lookup_entry.is_active,
            "metadata": {"source": "finance_universe", "lookup": lookup_entry.metadata if lookup_entry else {}},
        }
        ticker = Ticker.objects.create(symbol=symbol, **defaults)

    providers = build_default_providers(workspace=workspace, owner=owner)
    massive = providers.get("market_data_backup")
    company_name = _truncate_text(getattr(ticker, "name", "") or getattr(lookup_entry, "name", ""), 180)

    web_payload = _search_web_news(symbol, company_name, limit)
    web_entry = _upsert_news_cache(workspace, ticker, "web_search", web_payload)

    massive_payload: dict[str, Any]
    if massive is None or not hasattr(massive, "get_news"):
        massive_payload = _build_unavailable_payload(
            provider="massive",
            symbol=symbol,
            query=symbol,
            message="Massive news provider is unavailable.",
        )
    else:
        try:
            massive_payload = massive.get_news(symbol, limit=limit)
        except Exception as exc:
            logger.info(
                "finance massive news refresh failed symbol=%s workspace_id=%s owner_id=%s provider=%s error=%s",
                symbol,
                getattr(workspace, "id", ""),
                getattr(owner, "id", ""),
                getattr(massive, "provider_name", "massive"),
                exc,
            )
            massive_payload = _build_unavailable_payload(
                provider="massive",
                symbol=symbol,
                query=symbol,
                message=str(exc),
            )
    massive_payload = _normalize_massive_news(massive_payload, symbol)
    massive_entry = _upsert_news_cache(workspace, ticker, "massive", massive_payload)

    if web_entry.payload.get("status") == "unavailable" or massive_entry.payload.get("status") == "unavailable":
        logger.warning(
            "finance news refresh partial symbol=%s workspace_id=%s owner_id=%s web_status=%s massive_status=%s",
            symbol,
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            web_entry.payload.get("status") if isinstance(web_entry.payload, dict) else "",
            massive_entry.payload.get("status") if isinstance(massive_entry.payload, dict) else "",
        )

    return {
        "refreshed": True,
        "symbol": symbol,
        "status": "ok",
        "web_search_cache": _serialize_cache_entry(web_entry),
        "massive_cache": _serialize_cache_entry(massive_entry),
    }
