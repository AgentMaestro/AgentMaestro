from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, FinanceResearchSnapshot, Portfolio, Position, Ticker
from finance.providers.registry import build_default_providers

from .market_hours import get_schwab_market_hours_state, is_symbol_refreshable_now
from .bootstrap import (
    _build_position_rows,
    _collect_market_symbols,
    _find_default_portfolio,
    _find_default_watchlist,
    _quote_cache_key,
    _upsert_quote_cache,
    bootstrap_finance_workspace,
)


logger = get_app_logger("finance")


def _parse_timestamp(value: object) -> Any:
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    return value


def _portfolio_brokerage_refresh_due(portfolio: Portfolio) -> bool:
    synced_at = _parse_timestamp((portfolio.metadata or {}).get("schwab_synced_at"))
    if synced_at is None:
        return True
    ttl_seconds = max(0, int(getattr(settings, "FINANCE_BROKERAGE_REFRESH_TTL_SECONDS", 600)))
    return timezone.now() - synced_at >= timedelta(seconds=ttl_seconds)


def _quote_refresh_due(entry: FinanceDataCacheEntry | None) -> bool:
    if entry is None:
        return True
    if entry.expires_at is None:
        return True
    return entry.expires_at <= timezone.now()


def _snapshot_refresh_due(snapshot: FinanceResearchSnapshot | None) -> bool:
    if snapshot is None:
        return True
    if snapshot.expires_at is None:
        return True
    return snapshot.expires_at <= timezone.now()


def _quote_payload_last_price(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict) or str(payload.get("status") or "").strip().lower() == "unavailable":
        return None
    quote = payload.get("quote") or {}
    if isinstance(quote, dict):
        for value in (quote.get("last"), quote.get("last_price"), quote.get("price"), quote.get("close")):
            try:
                if value is not None:
                    numeric = float(value)
                    if numeric > 0:
                        return numeric
            except (TypeError, ValueError):
                continue
    for value in (
        payload.get("last_price"),
        payload.get("last"),
        payload.get("price"),
        payload.get("close"),
    ):
        try:
            if value is not None:
                numeric = float(value)
                if numeric > 0:
                    return numeric
        except (TypeError, ValueError):
            continue
    return None


def _refresh_brokerage_positions(*, workspace, owner, force: bool = False, rebuild_snapshot: bool = True) -> dict[str, Any]:
    portfolio = _find_default_portfolio(workspace, owner)
    watchlist = _find_default_watchlist(workspace, owner)
    if not force and not _portfolio_brokerage_refresh_due(portfolio):
        logger.info(
            "finance brokerage refresh skipped workspace_id=%s owner_id=%s reason=fresh",
            workspace.id,
            owner.id,
        )
        return {
            "refreshed": False,
            "workspace_id": str(workspace.id),
            "owner_id": str(owner.id),
            "position_count": portfolio.positions.count(),
        }

    providers = build_default_providers(workspace=workspace, owner=owner)
    broker = providers["brokerage"]
    logger.info(
        "finance brokerage refresh started workspace_id=%s owner_id=%s force=%s",
        workspace.id,
        owner.id,
        force,
    )
    account_snapshot = broker.list_accounts()
    primary_account_hash = str(account_snapshot.get("primary_account_hash") or "").strip()
    if not primary_account_hash:
        primary_account_hash = str((portfolio.metadata or {}).get("schwab_account_hash") or "").strip()

    balances_snapshot: dict[str, Any] = {}
    positions_snapshot: dict[str, Any] = {}
    transactions_snapshot: dict[str, Any] = {}
    orders_snapshot: dict[str, Any] = {}
    if primary_account_hash:
        balances_snapshot = broker.get_balances(account_id=primary_account_hash)
        positions_snapshot = broker.list_positions(account_id=primary_account_hash)
        logger.info(
            "finance brokerage transaction fetch started workspace_id=%s owner_id=%s account_hash=%s lookback_days=%s",
            workspace.id,
            owner.id,
            primary_account_hash,
            int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LOOKBACK_DAYS", 365)),
        )
        transactions_snapshot = broker.list_activity(
            account_id=primary_account_hash,
            limit=int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LIMIT", 100)),
        )
        logger.info(
            "finance brokerage transaction fetch finished workspace_id=%s owner_id=%s account_hash=%s status=%s count=%s",
            workspace.id,
            owner.id,
            primary_account_hash,
            transactions_snapshot.get("status") if isinstance(transactions_snapshot, dict) else "",
            len((transactions_snapshot.get("transactions") or []) if isinstance(transactions_snapshot, dict) else []),
        )
        logger.info(
            "finance brokerage order fetch started workspace_id=%s owner_id=%s account_hash=%s lookback_days=%s",
            workspace.id,
            owner.id,
            primary_account_hash,
            int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LOOKBACK_DAYS", 365)),
        )
        orders_snapshot = broker.list_orders(
            account_id=primary_account_hash,
            limit=int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LIMIT", 100)),
        )
        logger.info(
            "finance brokerage order fetch finished workspace_id=%s owner_id=%s account_hash=%s status=%s count=%s",
            workspace.id,
            owner.id,
            primary_account_hash,
            orders_snapshot.get("status") if isinstance(orders_snapshot, dict) else "",
            len((orders_snapshot.get("orders") or []) if isinstance(orders_snapshot, dict) else []),
        )

    broker_balances = balances_snapshot.get("balances") if isinstance(balances_snapshot, dict) else {}
    current_balances = broker_balances.get("current") if isinstance(broker_balances, dict) else {}
    initial_balances = broker_balances.get("initial") if isinstance(broker_balances, dict) else {}
    cash_available = (
        (current_balances or {}).get("cashAvailableForTrading")
        or (current_balances or {}).get("availableFunds")
        or (initial_balances or {}).get("cashAvailableForTrading")
        or (initial_balances or {}).get("availableFundsNonMarginableTrade")
        or (portfolio.metadata or {}).get("cash")
        or 0
    )
    portfolio.metadata = {
        **(portfolio.metadata or {}),
        "schwab_account_hash": primary_account_hash,
        "schwab_accounts": account_snapshot.get("accounts") or [],
        "cash": float(cash_available or 0),
        "market_value": float((positions_snapshot.get("raw") or {}).get("marketValue") or (portfolio.metadata or {}).get("market_value") or 0),
        "schwab_synced_at": timezone.now().isoformat(),
    }
    if isinstance(transactions_snapshot, dict) and transactions_snapshot.get("status") == "ok":
        portfolio.metadata["schwab_transactions"] = transactions_snapshot.get("transactions") or []
        portfolio.metadata["schwab_transactions_synced_at"] = timezone.now().isoformat()
        portfolio.metadata["schwab_transactions_count"] = len(portfolio.metadata["schwab_transactions"])
    if isinstance(orders_snapshot, dict) and orders_snapshot.get("status") == "ok":
        portfolio.metadata["schwab_orders"] = orders_snapshot.get("orders") or []
        portfolio.metadata["schwab_orders_synced_at"] = timezone.now().isoformat()
        portfolio.metadata["schwab_orders_count"] = len(portfolio.metadata["schwab_orders"])
    portfolio.save(update_fields=["metadata", "updated_at"])

    raw_positions = positions_snapshot.get("positions") if isinstance(positions_snapshot, dict) else []
    updated_count = 0
    seen_position_keys: set[tuple[str, str]] = set()
    if isinstance(raw_positions, list):
        for raw_position in raw_positions:
            if not isinstance(raw_position, dict):
                continue
            symbol = str(raw_position.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            ticker = Ticker.objects.filter(symbol=symbol).first()
            if ticker is None:
                ticker = Ticker.objects.create(
                    symbol=symbol,
                    name=str(raw_position.get("description") or "").strip(),
                    exchange="",
                    asset_type=Ticker.AssetType.EQUITY,
                    currency="USD",
                    is_active=True,
                    metadata={"source": "schwab"},
                )
            quantity = float(raw_position.get("quantity") or raw_position.get("long_quantity") or raw_position.get("longQuantity") or 0)
            if quantity == 0:
                continue
            average_price = float(raw_position.get("average_price") or raw_position.get("averagePrice") or raw_position.get("averageLongPrice") or 0)
            market_value = float(raw_position.get("market_value") or raw_position.get("marketValue") or 0)
            cost_basis = float(average_price * abs(quantity))
            side = Position.Side.LONG if quantity >= 0 else Position.Side.SHORT
            seen_position_keys.add((symbol, side))
            existing_position = Position.objects.filter(
                portfolio=portfolio,
                ticker=ticker,
                side=side,
            ).first()
            existing_metadata = dict(existing_position.metadata or {}) if existing_position is not None else {}
            position, _ = Position.objects.update_or_create(
                portfolio=portfolio,
                ticker=ticker,
                side=side,
                defaults={
                    "quantity": abs(quantity),
                    "average_cost": average_price,
                    "cost_basis": cost_basis,
                    "notes": "Synced from Schwab Trader API.",
                    "metadata": {
                        **existing_metadata,
                        "source": "schwab",
                        "account_hash": primary_account_hash,
                        "raw": raw_position,
                        "position_ttl": "infinite",
                    },
                },
            )
            position.save(update_fields=["quantity", "average_cost", "cost_basis", "notes", "metadata", "updated_at"])
            updated_count += 1

    deleted_count = 0
    for stale_position in Position.objects.filter(portfolio=portfolio).select_related("ticker"):
        stale_symbol = str(getattr(getattr(stale_position, "ticker", None), "symbol", "") or "").strip().upper()
        stale_side = str(stale_position.side or "").strip().upper()
        if (stale_symbol, stale_side) in seen_position_keys:
            continue
        stale_position.delete()
        deleted_count += 1

    logger.info(
        "finance brokerage refresh finished workspace_id=%s owner_id=%s positions=%s removed=%s transactions=%s orders=%s",
        workspace.id,
        owner.id,
        updated_count,
        deleted_count,
        len((portfolio.metadata or {}).get("schwab_transactions") or []),
        len((portfolio.metadata or {}).get("schwab_orders") or []),
    )
    snapshot_result = {"quote_status": "cached", "quote_count": 0, "position_count": updated_count}
    if rebuild_snapshot:
        snapshot_result = refresh_finance_snapshot(workspace=workspace, owner=owner, force=True)
    return {
        "refreshed": True,
        "workspace_id": str(workspace.id),
        "owner_id": str(owner.id),
        "account_hash": primary_account_hash,
        "position_count": updated_count,
        "transaction_count": len((portfolio.metadata or {}).get("schwab_transactions") or []),
        "order_count": len((portfolio.metadata or {}).get("schwab_orders") or []),
        "watchlist_count": watchlist.items.count(),
        "quote_status": snapshot_result.get("quote_status") or "cached",
        "quote_count": int(snapshot_result.get("quote_count") or 0),
    }


def _refresh_quote_cache(*, workspace, owner, force: bool = False, rebuild_snapshot: bool = True) -> dict[str, Any]:
    portfolio = _find_default_portfolio(workspace, owner)
    watchlist = _find_default_watchlist(workspace, owner)
    symbols = _collect_market_symbols(portfolio, watchlist)
    return refresh_finance_symbol_batch(workspace=workspace, owner=owner, symbols=symbols, force=force, rebuild_snapshot=rebuild_snapshot)


def refresh_finance_symbol_batch(*, workspace, owner, symbols: list[str], force: bool = False, rebuild_snapshot: bool = True) -> dict[str, Any]:
    normalized_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        candidate = str(symbol or "").strip().upper()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized_symbols.append(candidate)

    providers = build_default_providers(workspace=workspace, owner=owner)
    market_data = providers["market_data"]
    market_data_backup = providers.get("market_data_backup")
    ticker_map = {ticker.symbol.upper(): ticker for ticker in Ticker.objects.filter(symbol__in=normalized_symbols)}
    market_hours_state = get_schwab_market_hours_state(market_data=market_data)
    primary_quote_map: dict[str, dict[str, Any]] = {}
    backup_quote_map: dict[str, dict[str, Any]] = {}
    quotes: list[dict[str, Any]] = []
    refreshed_symbols: list[str] = []
    refreshed_details: list[str] = []
    skipped_symbols: list[str] = []
    deferred_symbols: list[str] = []
    quote_status = "cached"
    had_misses = False

    refreshable_symbols: list[str] = []
    for symbol in normalized_symbols:
        ticker = ticker_map.get(symbol)
        if ticker is None:
            continue
        allowed, reason = is_symbol_refreshable_now(ticker, market_hours_state)
        if allowed:
            cache_entry = (
                FinanceDataCacheEntry.objects.filter(
                    cache_key=_quote_cache_key(symbol),
                    data_kind=FinanceDataCacheEntry.DataKind.QUOTE,
                )
                .order_by("-as_of", "-created_at")
                .first()
            )
            if force or _quote_refresh_due(cache_entry):
                refreshable_symbols.append(symbol)
            else:
                skipped_symbols.append(symbol)
        else:
            deferred_symbols.append(symbol)
            logger.info(
                "finance quote refresh deferred workspace_id=%s owner_id=%s symbol=%s reason=%s",
                workspace.id,
                owner.id,
                symbol,
                reason,
            )

    if refreshable_symbols:
        try:
            primary_quote_map = market_data.get_quotes(refreshable_symbols)
        except NotImplementedError:
            primary_quote_map = {}
        except Exception as exc:
            logger.warning(
                "finance quote batch failed workspace_id=%s owner_id=%s provider=%s error=%s",
                workspace.id,
                owner.id,
                getattr(market_data, "provider_name", "market_data"),
                exc,
            )
            primary_quote_map = {}
        missing_symbols = [
            symbol
            for symbol in refreshable_symbols
            if _quote_payload_last_price(primary_quote_map.get(symbol)) is None
        ]
        if missing_symbols and market_data_backup is not None:
            try:
                backup_quote_map = market_data_backup.get_quotes(missing_symbols)
            except NotImplementedError:
                backup_quote_map = {}
            except Exception as exc:
                logger.warning(
                    "finance quote backup batch failed workspace_id=%s owner_id=%s provider=%s error=%s",
                    workspace.id,
                    owner.id,
                    getattr(market_data_backup, "provider_name", "market_data_backup"),
                    exc,
                )
                backup_quote_map = {}

    for symbol in normalized_symbols:
        ticker = ticker_map.get(symbol)
        if ticker is None:
            continue
        cache_entry = (
            FinanceDataCacheEntry.objects.filter(
                cache_key=_quote_cache_key(symbol),
                data_kind=FinanceDataCacheEntry.DataKind.QUOTE,
            )
            .order_by("-as_of", "-created_at")
            .first()
        )
        if symbol in deferred_symbols or symbol in skipped_symbols:
            if cache_entry:
                quote_payload = dict(cache_entry.payload or {})
                quote_status = "stale" if cache_entry.expires_at and cache_entry.expires_at <= timezone.now() else "cached"
                quotes.append(
                    {
                        "symbol": symbol,
                        "payload": quote_payload,
                        "cache_key": _quote_cache_key(symbol),
                    }
                )
            continue
        if not force and not _quote_refresh_due(cache_entry):
            skipped_symbols.append(symbol)
            continue
        quote_payload = None
        candidate = primary_quote_map.get(symbol) if primary_quote_map else None
        if _quote_payload_last_price(candidate) is not None:
            quote_payload = dict(candidate or {})
        else:
            candidate = backup_quote_map.get(symbol) if backup_quote_map else None
            if _quote_payload_last_price(candidate) is not None:
                quote_payload = dict(candidate or {})
        if quote_payload:
            cache_entry = _upsert_quote_cache(workspace, ticker, quote_payload)
            refreshed_symbols.append(symbol)
            quote_price = _quote_payload_last_price(quote_payload)
            quote_time = cache_entry.as_of.isoformat() if cache_entry.as_of else str(quote_payload.get("as_of") or quote_payload.get("timestamp") or "")
            if quote_price is not None:
                refreshed_details.append(f"{symbol}:{quote_price:.4f}@{quote_time}")
            else:
                refreshed_details.append(f"{symbol}@{quote_time}")
            quote_status = "refreshed"
        else:
            had_misses = True
            quote_status = "partial" if refreshed_symbols else "failed"

    if refreshed_symbols:
        quote_status = "partial" if had_misses else "refreshed"
    elif had_misses:
        quote_status = "failed"
    else:
        quote_status = "cached"

    snapshot_result = {"quote_status": quote_status, "quote_count": 0, "position_count": 0}
    if rebuild_snapshot and (refreshed_symbols or force):
        snapshot_result = refresh_finance_snapshot(workspace=workspace, owner=owner, force=True)
    logger.info(
        "finance quote batch result workspace_id=%s owner_id=%s refreshed=%s skipped_cache=%s deferred_market=%s",
        workspace.id,
        owner.id,
        ",".join(refreshed_details) if refreshed_details else "-",
        ",".join(skipped_symbols) if skipped_symbols else "-",
        ",".join(deferred_symbols) if deferred_symbols else "-",
    )
    return {
        "refreshed": bool(refreshed_symbols),
        "workspace_id": str(workspace.id),
        "owner_id": str(owner.id),
        "quote_status": quote_status,
        "quote_count": int(snapshot_result.get("quote_count") or 0),
        "position_count": int(snapshot_result.get("position_count") or 0),
        "refreshed_symbols": refreshed_symbols,
        "refreshed_details": refreshed_details,
        "skipped_due_to_cache": skipped_symbols,
        "skipped_symbols": skipped_symbols,
        "deferred_symbols": deferred_symbols,
        "market_hours_status": market_hours_state.get("status") if isinstance(market_hours_state, dict) else "unknown",
        "market_hours_source": market_hours_state.get("source") if isinstance(market_hours_state, dict) else "",
    }


def refresh_expired_quotes(*, workspace, owner, force: bool = False) -> dict[str, Any]:
    return _refresh_quote_cache(workspace=workspace, owner=owner, force=force)


def refresh_brokerage_snapshot(*, workspace, owner, force: bool = False) -> dict[str, Any]:
    return _refresh_brokerage_positions(workspace=workspace, owner=owner, force=force)


def refresh_finance_snapshot(*, workspace, owner, force: bool = False) -> dict[str, Any]:
    snapshot_key = f"workspace:{workspace.id}:finance-bootstrap"
    existing_snapshot = FinanceResearchSnapshot.objects.filter(snapshot_key=snapshot_key).first()
    if not force and not _snapshot_refresh_due(existing_snapshot):
        return {
            "workspace_id": str(workspace.id),
            "owner_id": str(owner.id),
            "snapshot_key": snapshot_key,
            "refreshed": False,
            "quote_status": (existing_snapshot.metadata or {}).get("quote_status") if existing_snapshot else "cached",
            "quote_count": len((existing_snapshot.payload or {}).get("quotes") or []) if existing_snapshot else 0,
            "position_count": len((existing_snapshot.payload or {}).get("positions") or []) if existing_snapshot else 0,
        }

    bootstrap = bootstrap_finance_workspace(workspace=workspace, owner=owner, refresh_quotes=False, refresh_brokerage=False)
    return {
        "workspace_id": str(workspace.id),
        "owner_id": str(owner.id),
        "snapshot_key": bootstrap.get("snapshot_key") or "",
        "refreshed": True,
        "quote_status": bootstrap.get("quote_status") or "cached",
        "quote_count": len(bootstrap.get("quotes") or []),
        "position_count": len(bootstrap.get("positions") or []),
    }


def refresh_finance_workspace(*, workspace, owner, force: bool = False) -> dict[str, Any]:
    brokerage_result = _refresh_brokerage_positions(workspace=workspace, owner=owner, force=force, rebuild_snapshot=False)
    quote_result = _refresh_quote_cache(workspace=workspace, owner=owner, force=force, rebuild_snapshot=False)
    snapshot = refresh_finance_snapshot(
        workspace=workspace,
        owner=owner,
        force=force or bool(brokerage_result.get("refreshed")) or bool(quote_result.get("refreshed")),
    )
    return {
        "ok": True,
        "workspace_id": str(workspace.id),
        "owner_id": str(owner.id),
        "brokerage_refreshed": bool(brokerage_result.get("refreshed")),
        "quote_refresh_count": len(quote_result.get("refreshed_symbols") or []),
        "quote_status": snapshot.get("quote_status") or quote_result.get("quote_status") or "cached",
        "position_count": int(snapshot.get("position_count") or 0),
        "quote_count": int(snapshot.get("quote_count") or 0),
    }
