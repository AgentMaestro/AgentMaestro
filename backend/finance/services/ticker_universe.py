from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, FinanceResearchSnapshot, Ticker, TickerUniverseEntry
from finance.providers.massive import MassiveMarketDataProvider
from finance.providers.registry import build_default_providers


logger = get_app_logger("finance")

MASSIVE_STOCK_TICKER_TYPES = [
    "CS",
    "PFD",
    "WARRANT",
    "ETF",
    "ADRC",
    "ADRP",
    "ADRW",
    "FUND",
    "BASKET",
    "OS",
]

MASSIVE_TICKER_UNIVERSE_MARKETS = [
    {
        "market": "stocks",
        "ticker_types": MASSIVE_STOCK_TICKER_TYPES,
    },
    {
        "market": "otc",
        "ticker_types": [],
    },
    {
        "market": "indices",
        "ticker_types": [],
    },
]


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


def _normalize_asset_type(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"STOCK", "EQUITY", "COMMON_STOCK", "CS", "COMMON"}:
        return "EQUITY"
    if text in {"ETF", "FUND", "INDEX", "OPTION", "CRYPTO", "OTHER"}:
        return text
    return "OTHER"


def _serialize_universe_entry(entry: TickerUniverseEntry) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "name": entry.name,
        "exchange": entry.exchange,
        "asset_type": entry.asset_type,
        "currency": entry.currency,
        "is_active": entry.is_active,
        "source_name": entry.source_name,
        "last_seen_at": entry.last_seen_at.isoformat() if entry.last_seen_at else "",
        "metadata": entry.metadata,
    }


def _history_payload_has_bars(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").strip().lower() == "unavailable":
        return False
    bars = payload.get("bars") or payload.get("candles") or []
    return isinstance(bars, list) and bool(bars)


def _history_cache_key(symbol: str, *, days: int = 250) -> str:
    return f"history:{symbol}:daily:{days}d"


def _fundamental_cache_key(symbol: str) -> str:
    return f"fundamental:{symbol}:schwab"


def _news_cache_key(symbol: str, source: str) -> str:
    return f"news:{_normalize_symbol(symbol)}:{_normalize_text(source).lower()}"


def _filings_cache_key(symbol: str, source: str) -> str:
    return f"filings:{_normalize_symbol(symbol)}:{_normalize_text(source).lower()}"


def _fundamental_payload_has_data(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").strip().lower() == "unavailable":
        return False
    fundamental = payload.get("fundamental")
    if isinstance(fundamental, dict) and fundamental:
        return True
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return True
    return any(key in payload for key in ("symbol", "high52", "low52", "marketCap", "peRatio", "eps"))


def _upsert_fundamental_cache(workspace, ticker: Ticker, fundamental_payload: dict[str, Any]) -> FinanceDataCacheEntry:
    now = timezone.now()
    expires_at = now + timedelta(seconds=getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400))
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_fundamental_cache_key(ticker.symbol),
        defaults={
            "workspace": workspace,
            "ticker": ticker,
            "data_kind": FinanceDataCacheEntry.DataKind.OTHER,
            "source_name": str(fundamental_payload.get("provider") or "finance"),
            "timeframe": "",
            "as_of": now,
            "expires_at": expires_at,
            "payload": fundamental_payload,
            "summary_text": f"{ticker.symbol} Schwab instrument fundamentals cached for finance research.",
            "response_hash": "",
            "metadata": {
                "bootstrap": False,
                "ttl_seconds": getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400),
                "projection": str(fundamental_payload.get("projection") or "fundamental"),
            },
        },
    )
    entry.refresh_from_db()
    logger.info(
        "finance fundamental cache upserted symbol=%s as_of=%s expires_at=%s ttl_seconds=%s",
        ticker.symbol,
        entry.as_of.isoformat() if entry.as_of else "",
        entry.expires_at.isoformat() if entry.expires_at else "",
        getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400),
    )
    return entry


def _upsert_filings_cache(workspace, ticker: Ticker, filings_payload: dict[str, Any]) -> FinanceDataCacheEntry:
    now = timezone.now()
    ttl_seconds = int(getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400))
    expires_at = now + timedelta(seconds=max(1, ttl_seconds))
    source_name = _normalize_text(filings_payload.get("provider") or "sec").lower() or "sec"
    existing_entry = FinanceDataCacheEntry.objects.filter(
        cache_key=_filings_cache_key(ticker.symbol, source_name),
        data_kind=FinanceDataCacheEntry.DataKind.FILINGS,
    ).first()
    payload = dict(filings_payload or {})
    if isinstance(getattr(existing_entry, "payload", None), dict):
        existing_summary_map = existing_entry.payload.get("ai_summaries")
        if isinstance(existing_summary_map, dict) and existing_summary_map:
            payload["ai_summaries"] = {
                **dict(existing_summary_map),
                **dict(payload.get("ai_summaries") or {}),
            }
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_filings_cache_key(ticker.symbol, source_name),
        defaults={
            "workspace": workspace,
            "ticker": ticker,
            "data_kind": FinanceDataCacheEntry.DataKind.FILINGS,
            "source_name": source_name,
            "timeframe": "",
            "as_of": now,
            "expires_at": expires_at,
            "payload": payload,
            "summary_text": f"{ticker.symbol} SEC filings cached for finance research.",
            "response_hash": "",
            "metadata": {
                "bootstrap": False,
                "ttl_seconds": ttl_seconds,
                "source": source_name,
                "filing_types": list(payload.get("filing_types") or []),
            },
        },
    )
    entry.refresh_from_db()
    logger.info(
        "finance filings cache upserted symbol=%s source=%s as_of=%s expires_at=%s ttl_seconds=%s",
        ticker.symbol,
        source_name,
        entry.as_of.isoformat() if entry.as_of else "",
        entry.expires_at.isoformat() if entry.expires_at else "",
        ttl_seconds,
    )
    return entry


def search_ticker_universe(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    term = _normalize_text(query)
    if not term:
        return []
    symbol_term = _normalize_symbol(term)
    candidates = list(
        TickerUniverseEntry.objects.filter(
            symbol__istartswith=symbol_term,
        ).order_by("-is_active", "symbol")[: max(limit, 10)]
    )
    if len(candidates) < max(limit, 10):
        name_candidates = list(
            TickerUniverseEntry.objects.filter(
                name__istartswith=term,
            ).order_by("-is_active", "symbol")[: max(limit, 10)]
        )
        seen = {item.symbol for item in candidates}
        for item in name_candidates:
            if item.symbol not in seen:
                candidates.append(item)
                seen.add(item.symbol)
    if len(candidates) < max(limit, 10):
        fallback_candidates = list(
            TickerUniverseEntry.objects.filter(
                symbol__icontains=symbol_term,
            ).order_by("-is_active", "symbol")[: max(limit, 10)]
        )
        seen = {item.symbol for item in candidates}
        for item in fallback_candidates:
            if item.symbol not in seen:
                candidates.append(item)
                seen.add(item.symbol)
    if len(candidates) < max(limit, 10):
        fallback_name_candidates = list(
            TickerUniverseEntry.objects.filter(
                name__icontains=term,
            ).order_by("-is_active", "symbol")[: max(limit, 10)]
        )
        seen = {item.symbol for item in candidates}
        for item in fallback_name_candidates:
            if item.symbol not in seen:
                candidates.append(item)
                seen.add(item.symbol)

    def _rank(entry: TickerUniverseEntry) -> tuple[int, str]:
        exact_symbol = entry.symbol.upper() == symbol_term
        exact_name = entry.name.lower() == term.lower()
        starts_symbol = entry.symbol.upper().startswith(symbol_term) if symbol_term else False
        starts_name = entry.name.lower().startswith(term.lower())
        if exact_symbol:
            score = 0
        elif exact_name:
            score = 1
        elif starts_symbol:
            score = 2
        elif starts_name:
            score = 3
        else:
            score = 4
        return (score, entry.symbol)

    candidates.sort(key=_rank)
    return [_serialize_universe_entry(entry) for entry in candidates[:limit]]


def build_ticker_lookup_context(symbol: str) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    if not symbol:
        return {
            "symbol": "",
            "status": "empty",
            "ticker": None,
            "quote_cache": None,
            "history_cache": None,
            "research_snapshot": None,
        }

    universe_entry = TickerUniverseEntry.objects.filter(symbol=symbol).first()
    ticker_entry = Ticker.objects.filter(symbol=symbol).first()
    base_entry = universe_entry or ticker_entry
    ticker_payload = _serialize_universe_entry(base_entry) if isinstance(base_entry, TickerUniverseEntry) else (
        _serialize_ticker(base_entry) if ticker_entry is not None else None
    )

    return {
        "symbol": symbol,
        "status": "hit" if base_entry else "miss",
        "ticker": ticker_payload,
        "quote_cache": None,
        "history_cache": None,
        "research_snapshot": None,
    }


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


def _upsert_history_cache(workspace, ticker: Ticker, history_payload: dict[str, Any], *, days: int = 250) -> FinanceDataCacheEntry:
    now = timezone.now()
    expires_at = now + timedelta(seconds=getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400))
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_history_cache_key(ticker.symbol, days=days),
        defaults={
            "workspace": workspace,
            "ticker": ticker,
            "data_kind": FinanceDataCacheEntry.DataKind.HISTORY,
            "source_name": str(history_payload.get("provider") or "finance"),
            "timeframe": f"daily:{days}d",
            "as_of": now,
            "expires_at": expires_at,
            "payload": history_payload,
            "summary_text": f"{ticker.symbol} daily history cached for finance research.",
            "response_hash": "",
            "metadata": {"bootstrap": False, "ttl_seconds": getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400)},
        },
    )
    entry.refresh_from_db()
    logger.info(
        "finance history cache upserted symbol=%s as_of=%s expires_at=%s ttl_seconds=%s",
        ticker.symbol,
        entry.as_of.isoformat() if entry.as_of else "",
        entry.expires_at.isoformat() if entry.expires_at else "",
        getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400),
    )
    return entry


def refresh_ticker_fundamentals(*, workspace, owner, symbol: str) -> dict[str, Any]:
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
    market = providers.get("market_data")
    if market is None or not hasattr(market, "get_instrument"):
        logger.error(
            "finance research fundamentals refresh unsupported workspace_id=%s owner_id=%s symbol=%s provider=%s",
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            symbol,
            getattr(market, "provider_name", "market_data") if market is not None else "market_data",
        )
        return {"refreshed": False, "symbol": symbol, "status": "unsupported"}

    fundamental_payload: dict[str, Any] | None = None
    try:
        fundamental_payload = market.get_instrument(symbol, projection="fundamental")
    except Exception as exc:
        logger.info(
            "finance research fundamentals refresh failed symbol=%s workspace_id=%s owner_id=%s provider=%s error=%s",
            symbol,
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            getattr(market, "provider_name", "market_data"),
            exc,
        )
        fundamental_payload = None

    if not _fundamental_payload_has_data(fundamental_payload):
        logger.error(
            "finance research fundamentals returned no data symbol=%s workspace_id=%s owner_id=%s provider=%s result=%s",
            symbol,
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            getattr(market, "provider_name", "market_data"),
            fundamental_payload if isinstance(fundamental_payload, dict) else {},
        )
        return {"refreshed": False, "symbol": symbol, "status": "empty"}

    fundamental_entry = _upsert_fundamental_cache(workspace, ticker, fundamental_payload)
    return {
        "refreshed": True,
        "symbol": symbol,
        "status": "ok",
        "cache_key": fundamental_entry.cache_key,
        "as_of": fundamental_entry.as_of.isoformat() if fundamental_entry.as_of else "",
        "expires_at": fundamental_entry.expires_at.isoformat() if fundamental_entry.expires_at else "",
    }


def refresh_ticker_filings(*, workspace, owner, symbol: str, filing_types: list[str] | None = None, limit: int = 12) -> dict[str, Any]:
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
    filings_provider = providers.get("filings")
    if filings_provider is None or not hasattr(filings_provider, "get_filings"):
        logger.error(
            "finance research filings refresh unsupported workspace_id=%s owner_id=%s symbol=%s provider=%s",
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            symbol,
            getattr(filings_provider, "provider_name", "filings") if filings_provider is not None else "filings",
        )
        return {"refreshed": False, "symbol": symbol, "status": "unsupported"}

    filing_types_list = [str(item).strip().upper() for item in (filing_types or ["10-K", "10-Q", "8-K"]) if str(item).strip()]
    try:
        filings_payload = filings_provider.get_filings(symbol, filing_types=filing_types_list, limit=max(1, int(limit or 12)))
    except Exception as exc:
        logger.info(
            "finance research filings refresh failed symbol=%s workspace_id=%s owner_id=%s provider=%s error=%s",
            symbol,
            getattr(workspace, "id", ""),
            getattr(owner, "id", ""),
            getattr(filings_provider, "provider_name", "filings"),
            exc,
        )
        filings_payload = {
            "provider": getattr(filings_provider, "provider_name", "sec"),
            "symbol": symbol,
            "status": "error",
            "message": str(exc),
            "count": 0,
            "filings": [],
            "filing_types": filing_types_list,
        }

    if not isinstance(filings_payload, dict):
        filings_payload = {
            "provider": getattr(filings_provider, "provider_name", "sec"),
            "symbol": symbol,
            "status": "empty",
            "message": "SEC filings provider returned no data.",
            "count": 0,
            "filings": [],
            "filing_types": filing_types_list,
        }
    else:
        filings_payload["filing_types"] = filing_types_list

    filings_entry = _upsert_filings_cache(workspace, ticker, filings_payload)
    return {
        "refreshed": True,
        "symbol": symbol,
        "status": str(filings_payload.get("status") or "ok"),
        "cache_key": filings_entry.cache_key,
        "as_of": filings_entry.as_of.isoformat() if filings_entry.as_of else "",
        "expires_at": filings_entry.expires_at.isoformat() if filings_entry.expires_at else "",
    }


def hydrate_ticker_research_context(
    symbol: str,
    *,
    workspace=None,
    owner=None,
    refresh_quote: bool = True,
    refresh_history: bool = True,
) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    if not symbol:
        return build_ticker_lookup_context(symbol)

    lookup = build_ticker_lookup_context(symbol)
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
    market = providers.get("market_data")
    now = timezone.now()

    quote_entry = (
        FinanceDataCacheEntry.objects.filter(
            ticker=ticker,
            data_kind=FinanceDataCacheEntry.DataKind.QUOTE,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    if refresh_quote and market is not None:
        try:
            quote_payload = market.get_quote(symbol)
            if quote_payload and isinstance(quote_payload, dict):
                quote_entry = _upsert_quote_cache(workspace, ticker, quote_payload)
        except Exception as exc:
            logger.info(
                "finance research quote refresh failed symbol=%s workspace_id=%s owner_id=%s error=%s",
                symbol,
                getattr(workspace, "id", ""),
                getattr(owner, "id", ""),
                exc,
            )

    history_entry = (
        FinanceDataCacheEntry.objects.filter(
            ticker=ticker,
            data_kind=FinanceDataCacheEntry.DataKind.HISTORY,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    fundamental_entry = (
        FinanceDataCacheEntry.objects.filter(
            cache_key=_fundamental_cache_key(symbol),
            data_kind=FinanceDataCacheEntry.DataKind.OTHER,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    news_web_entry = (
        FinanceDataCacheEntry.objects.filter(
            cache_key=_news_cache_key(symbol, "web_search"),
            data_kind=FinanceDataCacheEntry.DataKind.NEWS,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    news_massive_entry = (
        FinanceDataCacheEntry.objects.filter(
            cache_key=_news_cache_key(symbol, "massive"),
            data_kind=FinanceDataCacheEntry.DataKind.NEWS,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    filings_entry = (
        FinanceDataCacheEntry.objects.filter(
            cache_key=_filings_cache_key(symbol, "sec"),
            data_kind=FinanceDataCacheEntry.DataKind.FILINGS,
        )
        .order_by("-as_of", "-created_at")
        .first()
    )
    if refresh_history and market is not None:
        try:
            history_payload = market.get_history(
                symbol,
                timeframe="daily",
                start=now - timedelta(days=250),
                end=now,
            )
            if _history_payload_has_bars(history_payload):
                history_entry = _upsert_history_cache(workspace, ticker, history_payload, days=250)
        except Exception as exc:
            logger.info(
                "finance research history refresh failed symbol=%s workspace_id=%s owner_id=%s error=%s",
                symbol,
                getattr(workspace, "id", ""),
                getattr(owner, "id", ""),
                exc,
            )

    snapshot_key = f"workspace:{getattr(workspace, 'id', 'global')}:ticker:{symbol}:research"
    snapshot_entry = FinanceResearchSnapshot.objects.filter(snapshot_key=snapshot_key).first()
    quote_cache = _serialize_cache_entry(quote_entry)
    history_cache = _serialize_cache_entry(history_entry)
    fundamental_cache = _serialize_cache_entry(fundamental_entry)
    news_web_cache = _serialize_cache_entry(news_web_entry)
    news_massive_cache = _serialize_cache_entry(news_massive_entry)
    filings_cache = _serialize_cache_entry(filings_entry)
    snapshot_payload = {
        "ticker": lookup.get("ticker") or {
            "symbol": symbol,
            "name": ticker.name,
            "exchange": ticker.exchange,
            "asset_type": ticker.asset_type,
            "currency": ticker.currency,
            "is_active": ticker.is_active,
        },
        "quote_cache": quote_cache or {},
        "history_cache": history_cache or {},
        "fundamental_cache": fundamental_cache or {},
        "news_cache": {
            "web_search": news_web_cache or {},
            "massive": news_massive_cache or {},
        },
        "filings_cache": filings_cache or {},
        "chart": {
            "status": "placeholder",
            "message": "Daily OHLCV chart placeholder. Data will render here once the chart component is wired up.",
            "bar_count": len((history_cache or {}).get("payload", {}).get("bars") or (history_cache or {}).get("payload", {}).get("candles") or []),
            "as_of": (history_cache or {}).get("as_of", ""),
        },
        "fundamentals": {
            "schwab_instrument": (fundamental_cache or {}).get("payload") or {},
        },
        "news": [],
        "filings": [],
    }
    snapshot_summary = f"{symbol} research hydrated from cached lookup data"
    if history_entry and history_entry.as_of:
        snapshot_summary = f"{symbol} research hydrated with {history_entry.payload.get('count') if isinstance(history_entry.payload, dict) else ''} history bars".strip()
    if fundamental_entry and fundamental_entry.as_of:
        snapshot_summary = f"{symbol} research hydrated with cached fundamentals"
    if filings_entry and filings_entry.as_of:
        snapshot_summary = f"{symbol} research hydrated with cached SEC filings"
    snapshot_entry, _ = FinanceResearchSnapshot.objects.update_or_create(
        snapshot_key=snapshot_key,
        defaults={
            "workspace": workspace,
            "portfolio": None,
            "watchlist": None,
            "ticker": ticker,
            "snapshot_kind": FinanceResearchSnapshot.SnapshotKind.TICKER,
            "timeframe": "daily",
            "as_of": now,
            "expires_at": now + timedelta(seconds=getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400)),
            "summary_text": snapshot_summary,
            "payload": snapshot_payload,
            "source_keys": [
                (quote_cache or {}).get("cache_key", ""),
                (history_cache or {}).get("cache_key", ""),
                (fundamental_cache or {}).get("cache_key", ""),
                (news_web_cache or {}).get("cache_key", ""),
                (news_massive_cache or {}).get("cache_key", ""),
                (filings_cache or {}).get("cache_key", ""),
            ],
            "metadata": {
                "selected": True,
                "quote_status": "refreshed" if refresh_quote else "cached",
                "history_status": "refreshed" if refresh_history else "cached",
                "fundamentals_status": "cached" if fundamental_cache else "missing",
                "news_status": "cached" if news_web_cache or news_massive_cache else "missing",
                "filings_status": "cached" if filings_cache else "missing",
            },
        },
    )
    snapshot_entry.refresh_from_db()
    return {
        "symbol": symbol,
        "status": "hit" if lookup.get("status") == "hit" else "miss",
        "ticker": lookup.get("ticker"),
        "quote_cache": quote_cache,
        "history_cache": history_cache,
        "fundamental_cache": fundamental_cache,
        "news_cache": {
            "web_search": news_web_cache,
            "massive": news_massive_cache,
        },
        "filings_cache": filings_cache,
        "research_snapshot": {
            "snapshot_key": snapshot_entry.snapshot_key,
            "snapshot_kind": snapshot_entry.snapshot_kind,
            "timeframe": snapshot_entry.timeframe,
            "as_of": snapshot_entry.as_of.isoformat() if snapshot_entry.as_of else "",
            "expires_at": snapshot_entry.expires_at.isoformat() if snapshot_entry.expires_at else "",
            "summary_text": snapshot_entry.summary_text,
            "payload": snapshot_entry.payload,
            "metadata": snapshot_entry.metadata,
        },
    }


def build_ticker_research_context(symbol: str, *, workspace=None, owner=None) -> dict[str, Any]:
    return hydrate_ticker_research_context(
        symbol,
        workspace=workspace,
        owner=owner,
        refresh_quote=False,
        refresh_history=False,
    )


def refresh_ticker_history(*, workspace, owner, symbol: str, days: int = 250) -> dict[str, Any]:
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
    market = providers.get("market_data")
    backup = providers.get("market_data_backup")
    now = timezone.now()
    history_payload = None
    if market is not None:
        try:
            history_payload = market.get_history(
                symbol,
                timeframe="daily",
                start=now - timedelta(days=max(1, int(days))),
                end=now,
            )
        except Exception as exc:
            logger.info(
                "finance research history refresh failed symbol=%s workspace_id=%s owner_id=%s provider=%s error=%s",
                symbol,
                getattr(workspace, "id", ""),
                getattr(owner, "id", ""),
                getattr(market, "provider_name", "market_data"),
                exc,
            )
            history_payload = None
    if not _history_payload_has_bars(history_payload) and backup is not None:
        try:
            history_payload = backup.get_history(
                symbol,
                timeframe="daily",
                start=now - timedelta(days=max(1, int(days))),
                end=now,
            )
        except Exception as exc:
            logger.info(
                "finance research history backup refresh failed symbol=%s workspace_id=%s owner_id=%s provider=%s error=%s",
                symbol,
                getattr(workspace, "id", ""),
                getattr(owner, "id", ""),
                getattr(backup, "provider_name", "market_data_backup"),
                exc,
            )
            history_payload = None
    if not _history_payload_has_bars(history_payload):
        return {"refreshed": False, "symbol": symbol, "status": "empty"}
    history_entry = _upsert_history_cache(workspace, ticker, history_payload, days=days)
    return {
        "refreshed": True,
        "symbol": symbol,
        "status": "ok",
        "cache_key": history_entry.cache_key,
        "as_of": history_entry.as_of.isoformat() if history_entry.as_of else "",
        "expires_at": history_entry.expires_at.isoformat() if history_entry.expires_at else "",
        "bar_count": len((history_entry.payload or {}).get("bars") or (history_entry.payload or {}).get("candles") or []),
    }


def refresh_ticker_universe(*, max_pages: int | None = None, page_size: int | None = None) -> dict[str, Any]:
    provider = MassiveMarketDataProvider()
    pages_limit = max(1, int(max_pages or getattr(settings, "FINANCE_TICKER_UNIVERSE_MAX_PAGES", 50)))
    limit = max(1, int(page_size or getattr(settings, "FINANCE_TICKER_UNIVERSE_PAGE_SIZE", 1000)))
    total_rows = 0
    upserted_rows = 0
    pages = 0
    now = timezone.now()
    last_cursor = ""
    for market_spec in MASSIVE_TICKER_UNIVERSE_MARKETS:
        market = str(market_spec.get("market") or "stocks").strip().lower()
        ticker_types = list(market_spec.get("ticker_types") or [])
        market_types = ticker_types or [None]
        for ticker_type in market_types:
            cursor: str | None = None
            while pages < pages_limit:
                pages += 1
                response = provider.get_ticker_universe(
                    limit=limit,
                    cursor=cursor,
                    active=True,
                    market=market,
                    ticker_type=ticker_type,
                )
                rows = response.get("tickers") or []
                if not rows:
                    last_cursor = str(response.get("next_cursor") or "")
                    break
                entries: list[TickerUniverseEntry] = []
                for row in rows:
                    symbol = _normalize_symbol(row.get("symbol") or row.get("ticker"))
                    if not symbol:
                        continue
                    metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
                    metadata["massive_market"] = market
                    if ticker_type:
                        metadata.setdefault("massive_ticker_type", ticker_type)
                    entries.append(
                        TickerUniverseEntry(
                            symbol=symbol,
                            name=_truncate_text(row.get("name") or "", 180),
                            exchange=_truncate_text(row.get("exchange") or "", 64),
                            asset_type=_normalize_asset_type(row.get("asset_type")),
                            currency=_truncate_text(row.get("currency") or "USD", 8) or "USD",
                            is_active=bool(row.get("is_active", True)),
                            source_name="massive",
                            last_seen_at=now,
                            source_payload={**row, "market": market, "ticker_type": ticker_type or ""},
                            metadata=metadata,
                        )
                    )
                if entries:
                    with transaction.atomic():
                        TickerUniverseEntry.objects.bulk_create(
                            entries,
                            update_conflicts=True,
                            unique_fields=["symbol"],
                            update_fields=[
                                "name",
                                "exchange",
                                "asset_type",
                                "currency",
                                "is_active",
                                "source_name",
                                "last_seen_at",
                                "source_payload",
                                "metadata",
                                "updated_at",
                            ],
                        )
                    total_rows += len(entries)
                    upserted_rows += len(entries)
                last_cursor = str(response.get("next_cursor") or "")
                if not last_cursor:
                    break
                cursor = last_cursor
            if pages >= pages_limit:
                break
        if pages >= pages_limit:
            break

    logger.info(
        "finance ticker universe refresh finished pages=%s rows=%s upserted=%s next_cursor=%s markets=%s types=%s",
        pages,
        total_rows,
        upserted_rows,
        last_cursor or "",
        ",".join(spec["market"] for spec in MASSIVE_TICKER_UNIVERSE_MARKETS),
        ",".join(MASSIVE_STOCK_TICKER_TYPES),
    )
    return {
        "provider": provider.provider_name,
        "pages": pages,
        "rows": total_rows,
        "upserted": upserted_rows,
        "next_cursor": last_cursor,
        "finished": not bool(last_cursor),
        "as_of": now.isoformat(),
        "ticker_types": MASSIVE_STOCK_TICKER_TYPES,
        "ticker_markets": [spec["market"] for spec in MASSIVE_TICKER_UNIVERSE_MARKETS],
    }
