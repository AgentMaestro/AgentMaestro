from __future__ import annotations

from calendar import monthrange
from datetime import date as date_class
from datetime import datetime, time as time_class, timedelta
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, Ticker

logger = get_app_logger("finance")

_DEFAULT_MARKETS = ("equity", "option", "bond", "future", "forex")
_CONTINUOUS_SYMBOLS = {"BTC", "BTCUSD", "BTC-USD", "BTC/USD"}
_QUOTE_WINDOW_START = time_class(4, 0)
_QUOTE_WINDOW_END = time_class(20, 0)


def _market_hours_cache_key(day: date_class) -> str:
    return f"schwab:market-hours:{day.isoformat()}"


def _market_hours_expires_at(now: datetime) -> datetime:
    local_now = timezone.localtime(now)
    next_day = local_now.date() + timedelta(days=1)
    return timezone.make_aware(datetime.combine(next_day, time_class.min), local_now.tzinfo)


def _observed_date(day: date_class) -> date_class:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> date_class:
    first_day = date_class(year, month, 1)
    offset = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date_class:
    last_day = date_class(year, month, monthrange(year, month)[1])
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _is_us_federal_holiday(day: date_class) -> bool:
    year = day.year
    fixed_holidays = {
        _observed_date(date_class(year, 1, 1)),
        _observed_date(date_class(year, 6, 19)),
        _observed_date(date_class(year, 7, 4)),
        _observed_date(date_class(year, 11, 11)),
        _observed_date(date_class(year, 12, 25)),
    }
    floating_holidays = {
        _nth_weekday_of_month(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday_of_month(year, 2, 0, 3),   # Washington's Birthday
        _last_weekday_of_month(year, 5, 0),     # Memorial Day
        _nth_weekday_of_month(year, 9, 0, 1),   # Labor Day
        _nth_weekday_of_month(year, 10, 0, 2),   # Columbus Day / Indigenous Peoples' Day
        _nth_weekday_of_month(year, 11, 3, 4),   # Thanksgiving
    }
    return day in fixed_holidays or day in floating_holidays


def _is_equity_quote_day(day: date_class) -> bool:
    return day.weekday() < 5 and not _is_us_federal_holiday(day)


def _is_equity_quote_window_open(now: datetime) -> bool:
    local_now = timezone.localtime(now)
    current_time = local_now.time()
    return _QUOTE_WINDOW_START <= current_time < _QUOTE_WINDOW_END


def next_equity_quote_purge_cutoff(now: datetime | None = None) -> datetime:
    candidate_now = now or timezone.now()
    local_now = timezone.localtime(candidate_now)
    candidate_day = local_now.date()
    if local_now.time() >= _QUOTE_WINDOW_START:
        candidate_day = candidate_day + timedelta(days=1)
    while candidate_day.weekday() >= 5 or _is_us_federal_holiday(candidate_day):
        candidate_day = candidate_day + timedelta(days=1)
    return timezone.make_aware(
        datetime.combine(candidate_day, _QUOTE_WINDOW_START),
        local_now.tzinfo,
    )


def _load_cached_market_hours(day: date_class) -> FinanceDataCacheEntry | None:
    return (
        FinanceDataCacheEntry.objects.filter(
            cache_key=_market_hours_cache_key(day),
            data_kind=FinanceDataCacheEntry.DataKind.OTHER,
        )
        .order_by("-created_at")
        .first()
    )


def _store_market_hours(day: date_class, payload: dict[str, Any], *, provider_name: str, markets: list[str]) -> FinanceDataCacheEntry:
    now = timezone.now()
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_market_hours_cache_key(day),
        defaults={
            "workspace": None,
            "ticker": None,
            "portfolio": None,
            "watchlist": None,
            "data_kind": FinanceDataCacheEntry.DataKind.OTHER,
            "source_name": provider_name,
            "timeframe": "daily",
            "as_of": now,
            "expires_at": _market_hours_expires_at(now),
            "payload": payload,
            "summary_text": f"{provider_name} market hours cache for {day.isoformat()}",
            "response_hash": "",
            "metadata": {
                "provider": provider_name,
                "markets": list(markets),
                "requested_date": day.isoformat(),
                "source": "market_hours",
            },
        },
    )
    return entry


def get_schwab_market_hours_state(
    *,
    market_data=None,
    markets: list[str] | None = None,
    date_value: date_class | None = None,
) -> dict[str, Any]:
    day = date_value or timezone.localdate()
    requested_markets = [str(market or "").strip().lower() for market in (markets or _DEFAULT_MARKETS) if str(market or "").strip()]
    if not requested_markets:
        requested_markets = list(_DEFAULT_MARKETS)

    cached_entry = _load_cached_market_hours(day)
    now = timezone.now()
    if cached_entry is not None and (cached_entry.expires_at is None or cached_entry.expires_at > now):
        return {
            "status": "ok",
            "source": "cache",
            "date": day.isoformat(),
            "markets": requested_markets,
            "cache_key": cached_entry.cache_key,
            "expires_at": cached_entry.expires_at.isoformat() if cached_entry.expires_at else "",
            "payload": dict(cached_entry.payload or {}),
        }

    provider = market_data
    if provider is None:
        from finance.providers.registry import build_default_providers

        provider = build_default_providers()["market_data"]

    payload: dict[str, Any] | None = None
    try:
        payload_candidate = provider.get_market_hours(requested_markets, date=day)
        if isinstance(payload_candidate, dict):
            payload = payload_candidate
    except NotImplementedError:
        payload = None
    except Exception as exc:
        logger.warning(
            "finance market hours refresh failed provider=%s date=%s markets=%s error=%s",
            getattr(provider, "provider_name", "market_data"),
            day.isoformat(),
            requested_markets,
            exc,
        )
        payload = None

    if payload:
        _store_market_hours(day, payload, provider_name=getattr(provider, "provider_name", "market_data"), markets=requested_markets)
        logger.info(
            "finance market hours refreshed provider=%s date=%s markets=%s",
            getattr(provider, "provider_name", "market_data"),
            day.isoformat(),
            requested_markets,
        )
        return {
            "status": "ok",
            "source": "live",
            "date": day.isoformat(),
            "markets": requested_markets,
            "payload": payload,
            "expires_at": _market_hours_expires_at(now).isoformat(),
        }

    if cached_entry is not None:
        logger.info(
            "finance market hours cache reused provider=%s date=%s markets=%s",
            cached_entry.source_name or "market_data",
            day.isoformat(),
            requested_markets,
        )
        return {
            "status": "ok",
            "source": "cache_stale",
            "date": day.isoformat(),
            "markets": requested_markets,
            "cache_key": cached_entry.cache_key,
            "expires_at": cached_entry.expires_at.isoformat() if cached_entry.expires_at else "",
            "payload": dict(cached_entry.payload or {}),
        }

    logger.warning(
        "finance market hours unavailable provider=%s date=%s markets=%s",
        getattr(provider, "provider_name", "market_data"),
        day.isoformat(),
        requested_markets,
    )
    return {
        "status": "unavailable",
        "source": "live",
        "date": day.isoformat(),
        "markets": requested_markets,
        "payload": {},
        "message": "Market hours could not be loaded.",
    }


def describe_market_hours(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"is_open": False, "sessions": []}
    state_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    sessions: list[dict[str, Any]] = []
    for market_name, market_payload in state_payload.items():
        if not isinstance(market_payload, dict):
            continue
        for product_name, entry in market_payload.items():
            if not isinstance(entry, dict):
                continue
            session_hours = entry.get("sessionHours") if isinstance(entry.get("sessionHours"), dict) else {}
            session_rows: list[dict[str, Any]] = []
            for session_name, windows in session_hours.items():
                if not isinstance(windows, list):
                    continue
                for window in windows:
                    if not isinstance(window, dict):
                        continue
                    session_rows.append(
                        {
                            "session": session_name,
                            "start": window.get("start"),
                            "end": window.get("end"),
                        }
                    )
            sessions.append(
                {
                    "market": market_name,
                    "product": product_name,
                    "is_open": bool(entry.get("isOpen")),
                    "session_hours": session_rows,
                }
            )
    return {
        "is_open": any(item.get("is_open") for item in sessions),
        "sessions": sessions,
    }


def _market_name_for_ticker(ticker: Ticker) -> str:
    asset_type = str(getattr(ticker, "asset_type", "") or "").strip().upper()
    if asset_type in {Ticker.AssetType.CRYPTO}:
        return ""
    if asset_type in {Ticker.AssetType.OPTION}:
        return "option"
    if asset_type in {Ticker.AssetType.EQUITY, Ticker.AssetType.ETF, Ticker.AssetType.INDEX, Ticker.AssetType.FUND, Ticker.AssetType.OTHER}:
        return "equity"
    if asset_type == "FUTURE":
        return "future"
    if asset_type == "FOREX":
        return "forex"
    return "equity"


def _is_continuous_symbol(ticker: Ticker) -> bool:
    asset_type = str(getattr(ticker, "asset_type", "") or "").strip().upper()
    if asset_type == Ticker.AssetType.CRYPTO:
        return True
    symbol = str(getattr(ticker, "symbol", "") or "").strip().upper()
    if symbol in _CONTINUOUS_SYMBOLS or symbol.startswith("BTC"):
        return True
    metadata = dict(getattr(ticker, "metadata", {}) or {})
    if str(metadata.get("market") or "").strip().lower() == "24h":
        return True
    if str(metadata.get("asset_class") or "").strip().lower() == "crypto":
        return True
    return False


def _market_entry_is_open(entry: dict[str, Any], now: datetime) -> bool:
    session_hours = entry.get("sessionHours")
    if isinstance(session_hours, dict):
        for windows in session_hours.values():
            if not isinstance(windows, list):
                continue
            for window in windows:
                if not isinstance(window, dict):
                    continue
                start = parse_datetime(str(window.get("start") or ""))
                end = parse_datetime(str(window.get("end") or ""))
                if start is None or end is None:
                    continue
                if start <= now <= end:
                    return True
    return bool(entry.get("isOpen"))


def _market_hours_payload_body(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        return nested_payload
    return payload


def _market_is_open_today(payload: dict[str, Any], market_name: str) -> bool:
    payload = _market_hours_payload_body(payload)
    market_payload = payload.get(market_name) or payload.get(market_name.upper()) or {}
    if not isinstance(market_payload, dict):
        return False
    entries = [value for value in market_payload.values() if isinstance(value, dict)]
    if not entries:
        return bool(market_payload.get("isOpen"))
    return True


def _market_is_open(payload: dict[str, Any], market_name: str, now: datetime) -> bool:
    payload = _market_hours_payload_body(payload)
    market_payload = payload.get(market_name) or payload.get(market_name.upper()) or {}
    if not isinstance(market_payload, dict):
        return False
    entries = [value for value in market_payload.values() if isinstance(value, dict)]
    if not entries:
        return bool(market_payload.get("isOpen"))
    return any(_market_entry_is_open(entry, now) for entry in entries)


def is_symbol_refreshable_now(ticker: Ticker, market_hours_state: dict[str, Any] | None, *, now: datetime | None = None) -> tuple[bool, str]:
    if _is_continuous_symbol(ticker):
        return True, "continuous_asset"

    market_name = _market_name_for_ticker(ticker)
    if not market_name:
        return True, "no_market_mapping"

    candidate_now = now or timezone.now()
    local_now = timezone.localtime(candidate_now)
    state_payload = _market_hours_payload_body(market_hours_state.get("payload") if isinstance(market_hours_state, dict) else None)
    state_status = str((market_hours_state or {}).get("status") or "").strip().lower() if isinstance(market_hours_state, dict) else ""
    if market_name in {"equity", "option"}:
        if not _is_equity_quote_day(local_now.date()):
            return False, "market_closed_today"
        if not _is_equity_quote_window_open(candidate_now):
            return False, "quote_window_closed"
        if not isinstance(state_payload, dict):
            return True, "quote_window_open"
        if state_status == "unavailable":
            return True, "quote_window_open"
        if _market_is_open_today(state_payload, market_name):
            return True, "quote_window_open"
        return False, "market_closed"

    if not isinstance(state_payload, dict):
        return True, "market_hours_unavailable"
    if state_status == "unavailable":
        return True, "market_hours_unavailable"
    if _market_is_open(state_payload, market_name, candidate_now):
        return True, "market_open"
    return False, "market_closed"
