from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
import re
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from logging_utils import get_app_logger

from finance.models import FinanceDataCacheEntry, FinanceResearchSnapshot, Portfolio, Position, Ticker, Watchlist
from finance.providers.registry import build_default_providers


logger = get_app_logger("finance")


def _format_context_datetime(value: object) -> str:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
    if parsed is None:
        return ""
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    local_dt = timezone.localtime(parsed)
    hour = local_dt.strftime("%I").lstrip("0") or "0"
    return f"{local_dt.strftime('%b')} {local_dt.day}, {local_dt.year} {hour}:{local_dt.strftime('%M %p %Z')}"


def _format_context_currency(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "+" if numeric > 0 else ""
    abs_value = abs(numeric)
    return f"{sign}${abs_value:,.2f}" if numeric >= 0 else f"-${abs_value:,.2f}"


def _format_position_context_lines(positions: list[Any]) -> list[str]:
    if not positions:
        return ["- No open positions loaded yet."]

    lines: list[str] = []
    max_positions = 12
    for position in positions[:max_positions]:
        if isinstance(position, dict):
            symbol = str(position.get("symbol") or "").strip()
            side = str(position.get("side") or "").strip()
            quantity = position.get("quantity") or 0
            average_cost = position.get("average_cost") or 0
            cost_basis = position.get("cost_basis") or 0
            notes = str(position.get("notes") or "").strip()
            metadata = dict(position.get("metadata") or {})
            last_price = position.get("last_price")
            last_price_as_of = position.get("last_price_as_of") or position.get("quote_as_of") or position.get("quote_timestamp")
            gain_amount = position.get("gain_amount")
            gain_percent = position.get("gain_percent")
        else:
            symbol = position.ticker.symbol
            side = position.side
            quantity = position.quantity
            average_cost = position.average_cost
            cost_basis = position.cost_basis
            notes = str(position.notes or "").strip()
            metadata = position.metadata or {}
            last_price = None
            last_price_as_of = None
            gain_amount = None
            gain_percent = None
        basis_value = _derive_cost_basis(average_cost, quantity, cost_basis)
        parts = [
            f"- {symbol}",
            f"side={str(side).lower()}",
            f"qty={float(quantity):g}",
            f"entry={float(average_cost):.4f}",
            f"basis={float(basis_value):.2f}",
            "position_ttl=infinite",
        ]
        if last_price is not None:
            try:
                parts.append(f"last={float(last_price):.4f}")
            except (TypeError, ValueError):
                pass
        last_price_dt = _format_context_datetime(last_price_as_of)
        if last_price_dt:
            parts.append(f"last_as_of={last_price_dt}")
        if gain_amount is not None:
            pnl_display = _format_context_currency(gain_amount)
            if pnl_display:
                parts.append(f"pnl={pnl_display}")
        if gain_percent is not None:
            try:
                parts.append(f"pnl_pct={float(gain_percent):+.1f}%")
            except (TypeError, ValueError):
                pass
        entry_commission = metadata.get("entry_commission")
        if entry_commission is not None:
            try:
                parts.append(f"commission={float(entry_commission):.2f}")
            except (TypeError, ValueError):
                parts.append(f"commission={entry_commission}")
        notes = " ".join(notes.split())
        if notes:
            parts.append(f"notes={notes}")
        lines.append(" ".join(parts))

    if len(positions) > max_positions:
        lines.append(f"- ... {len(positions) - max_positions} more positions not shown")

    return lines


def _derive_cost_basis(average_cost: object, quantity: object, fallback: object = 0) -> float:
    try:
        derived = float(average_cost or 0) * float(quantity or 0)
    except (TypeError, ValueError):
        derived = 0.0
    if derived > 0:
        return derived
    try:
        return float(fallback or 0)
    except (TypeError, ValueError):
        return 0.0


def _portfolio_brokerage_refresh_due(portfolio: Portfolio) -> bool:
    synced_at = parse_datetime(str((portfolio.metadata or {}).get("schwab_synced_at") or ""))
    if synced_at is None:
        return True
    ttl_seconds = max(0, int(getattr(settings, "FINANCE_BROKERAGE_REFRESH_TTL_SECONDS", 600)))
    return timezone.now() - synced_at >= timedelta(seconds=ttl_seconds)


def build_finance_system_context_overlay(bootstrap: dict[str, Any], *, include_watchlist: bool = False) -> str:
    portfolio = bootstrap.get("portfolio") or {}
    watchlist = bootstrap.get("watchlist") or {}
    source_policy = bootstrap.get("source_policy") or {}
    quotes = bootstrap.get("quotes") or []
    positions = bootstrap.get("positions") or []
    position_rows = bootstrap.get("position_rows") or []
    transactions = bootstrap.get("transactions") or []
    lines = [
        "Finance context:",
        f"- Workspace: {bootstrap.get('workspace_name') or 'unknown'}",
        f"- Portfolio: {portfolio.get('name') or 'default'}",
        f"- Positions loaded: {portfolio.get('position_count') or len(positions) or len(position_rows)}",
        f"- Quotes loaded: {len(quotes)}",
        f"- Transactions loaded: {len(transactions)}",
        f"- Position TTL: {source_policy.get('positions_ttl') or 'infinite'}",
        f"- Quote TTL seconds: {source_policy.get('quotes_ttl_seconds') or getattr(settings, 'FINANCE_QUOTE_TTL_SECONDS', 120)}",
        f"- Brokerage TTL seconds: {source_policy.get('brokerage_ttl_seconds') or getattr(settings, 'FINANCE_BROKERAGE_REFRESH_TTL_SECONDS', 600)}",
        f"- Snapshot TTL seconds: {source_policy.get('research_snapshot_ttl_seconds') or getattr(settings, 'FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS', 300)}",
        "- Brokerage positions are authoritative until a confirmed trade or broker sync updates them.",
        "- Use finance tools again if the user asks for a fresh quote, refreshed position read, or narrower ticker research.",
        "- Watchlist items are tracked separately and are not included in the first-turn finance system context unless explicitly requested.",
        "- Current portfolio positions with last price, quote timestamp, and return context:",
    ]
    lines.extend(_format_position_context_lines(position_rows or positions))
    if include_watchlist:
        lines.insert(4, f"- Watchlist items tracked in background: {watchlist.get('count') or 0}")
    return "\n".join(lines)


def _find_default_portfolio(workspace, owner) -> Portfolio:
    portfolio = (
        Portfolio.objects.filter(workspace=workspace, owner=owner)
        .order_by("-is_default", "created_at")
        .first()
    )
    if portfolio:
        return portfolio
    return Portfolio.objects.create(
        workspace=workspace,
        owner=owner,
        name="Default Portfolio",
        description="AI-managed default portfolio",
        base_currency="USD",
        is_default=True,
        metadata={"cash": 0, "market_value": 0},
    )


def _find_default_watchlist(workspace, owner) -> Watchlist:
    watchlist = (
        Watchlist.objects.filter(workspace=workspace, owner=owner)
        .order_by("-is_default", "created_at")
        .first()
    )
    if watchlist:
        return watchlist
    return Watchlist.objects.create(
        workspace=workspace,
        owner=owner,
        name="AI Watchlist",
        description="AI-managed watchlist",
        is_default=True,
    )


def _serialize_ticker(ticker: Ticker) -> dict[str, Any]:
    return {
        "symbol": ticker.symbol,
        "name": ticker.name,
        "exchange": ticker.exchange,
        "asset_type": ticker.asset_type,
        "currency": ticker.currency,
        "is_active": ticker.is_active,
    }


def _serialize_position(position: Position) -> dict[str, Any]:
    cost_basis = _derive_cost_basis(position.average_cost, position.quantity, position.cost_basis)
    payload = {
        "symbol": position.ticker.symbol,
        "side": position.side,
        "quantity": float(position.quantity),
        "average_cost": float(position.average_cost),
        "cost_basis": float(cost_basis),
        "notes": position.notes,
        "position_ttl": "infinite",
    }
    if position.metadata:
        payload["metadata"] = position.metadata
    return payload


def _extract_quote_last_price(quote_payload: dict[str, Any]) -> float | None:
    if not isinstance(quote_payload, dict):
        return None
    quote = quote_payload.get("quote") or {}
    if isinstance(quote, dict):
        for value in (
            quote.get("last"),
            quote.get("last_price"),
            quote.get("price"),
            quote.get("close"),
            quote.get("trade_price"),
            quote.get("bid"),
            quote.get("ask"),
        ):
            try:
                if value is not None:
                    numeric = float(value)
                    if numeric > 0:
                        return numeric
            except (TypeError, ValueError):
                continue
    snapshot = quote_payload.get("snapshot") or {}
    if isinstance(snapshot, dict):
        for nested in (
            snapshot.get("min"),
            snapshot.get("day"),
            snapshot.get("prevDay"),
            snapshot.get("prev_day"),
            snapshot.get("lastTrade"),
            snapshot.get("last_trade"),
        ):
            if not isinstance(nested, dict):
                continue
            for value in (
                nested.get("c"),
                nested.get("close"),
                nested.get("price"),
                nested.get("last"),
                nested.get("trade_price"),
            ):
                try:
                    if value is not None:
                        numeric = float(value)
                        if numeric > 0:
                            return numeric
                except (TypeError, ValueError):
                    continue
    for value in (
        quote_payload.get("last_price"),
        quote_payload.get("last"),
        quote_payload.get("price"),
        quote_payload.get("close"),
        quote_payload.get("trade_price"),
        quote_payload.get("snapshot", {}).get("min", {}).get("c") if isinstance(quote_payload.get("snapshot"), dict) else None,
        quote_payload.get("snapshot", {}).get("day", {}).get("c") if isinstance(quote_payload.get("snapshot"), dict) else None,
        quote_payload.get("snapshot", {}).get("prevDay", {}).get("c") if isinstance(quote_payload.get("snapshot"), dict) else None,
        quote_payload.get("price"),
    ):
        try:
            if value is not None:
                numeric = float(value)
                if numeric > 0:
                    return numeric
        except (TypeError, ValueError):
            continue
    return None


def _build_position_rows(positions: list[Any], quote_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        if isinstance(position, dict):
            symbol = str(position.get("symbol") or "").strip().upper()
            side = str(position.get("side") or "LONG").strip().upper()
            quantity = float(position.get("quantity") or 0)
            average_cost = float(position.get("average_cost") or 0)
            cost_basis = float(position.get("cost_basis") or 0)
            metadata = dict(position.get("metadata") or {})
            notes = str(position.get("notes") or "").strip()
        else:
            symbol = str(position.ticker.symbol).strip().upper()
            side = str(position.side or "LONG").strip().upper()
            quantity = float(position.quantity or 0)
            average_cost = float(position.average_cost or 0)
            cost_basis = float(position.cost_basis or 0)
            metadata = dict(position.metadata or {})
            notes = str(position.notes or "").strip()
        if not symbol:
            continue
        quote_entry = quote_map.get(symbol) or {}
        quote_payload = {}
        if isinstance(quote_entry, dict):
            if isinstance(quote_entry.get("payload"), dict):
                quote_payload = dict(quote_entry.get("payload") or {})
            else:
                quote_payload = dict(quote_entry or {})
        last_price = _extract_quote_last_price(quote_payload)
        quote_as_of = quote_entry.get("as_of") or quote_payload.get("as_of") or quote_payload.get("timestamp") or quote_payload.get("updated")
        commissions = metadata.get("entry_commission")
        try:
            commissions_value = float(commissions) if commissions is not None else 0.0
        except (TypeError, ValueError):
            commissions_value = 0.0
        market_value = float(last_price * quantity) if last_price is not None else None
        basis_value = _derive_cost_basis(average_cost, quantity, cost_basis)
        basis_and_commissions = float(basis_value or 0) + commissions_value
        gain_amount = (market_value - float(basis_value or 0) - commissions_value) if market_value is not None else None
        gain_percent = (
            100.0 * ((market_value / basis_and_commissions) - 1.0)
            if market_value is not None and basis_and_commissions > 0
            else None
        )
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "average_cost": average_cost,
                "last_price": last_price,
                "last_price_as_of": quote_as_of,
                "cost_basis": basis_value,
                "commissions": commissions_value,
                "market_value": market_value,
                "gain_amount": gain_amount,
                "gain_percent": gain_percent,
                "notes": notes,
                "quote": quote_payload,
                "position_ttl": "infinite",
            }
        )
    return rows


def _transaction_sources(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(transaction, dict):
        sources.append(transaction)
        for key in ("orderLegCollection", "orderLegs", "legs", "executionLegs", "orderActivityCollection", "activities"):
            nested = transaction.get(key)
            if isinstance(nested, list):
                for item in nested:
                    if not isinstance(item, dict):
                        continue
                    sources.append(item)
                    instrument = item.get("instrument")
                    if isinstance(instrument, dict):
                        sources.append(instrument)
                    execution_legs = item.get("executionLegs")
                    if isinstance(execution_legs, list):
                        for execution_leg in execution_legs:
                            if isinstance(execution_leg, dict):
                                sources.append(execution_leg)
            elif isinstance(nested, dict):
                sources.append(nested)
                instrument = nested.get("instrument")
                if isinstance(instrument, dict):
                    sources.append(instrument)
                execution_legs = nested.get("executionLegs")
                if isinstance(execution_legs, list):
                    for execution_leg in execution_legs:
                        if isinstance(execution_leg, dict):
                            sources.append(execution_leg)
        transaction_item = transaction.get("transactionItem")
        if isinstance(transaction_item, dict):
            sources.append(transaction_item)
            nested_instrument = transaction_item.get("instrument")
            if isinstance(nested_instrument, dict):
                sources.append(nested_instrument)
        instrument = transaction.get("instrument")
        if isinstance(instrument, dict):
            sources.append(instrument)
        transfer_items = transaction.get("transferItems")
        if isinstance(transfer_items, list):
            for item in transfer_items:
                if isinstance(item, dict):
                    sources.append(item)
                    break
    return sources


def _transaction_value(transaction: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for source in _transaction_sources(transaction):
        for key in keys:
            value = source.get(key)
            if value not in (None, "", []):
                return value
    return None


def _transaction_timestamp(transaction: dict[str, Any]) -> str:
    raw_value = _transaction_value(
        transaction,
        (
            "transactionDateTime",
            "tradeDateTime",
            "transactionDate",
            "tradeDate",
            "date",
            "settlementDate",
            "enteredTime",
            "filledTime",
            "closeTime",
            "executionTime",
            "time",
        ),
    )
    if isinstance(raw_value, datetime):
        return raw_value.isoformat()
    if isinstance(raw_value, str):
        parsed = parse_datetime(raw_value)
        if parsed is not None:
            return parsed.isoformat()
        try:
            parsed_dt = datetime.fromisoformat(raw_value)
            return parsed_dt.isoformat()
        except ValueError:
            return raw_value
    return ""


def _transaction_symbol(transaction: dict[str, Any]) -> str:
    raw_value = _transaction_value(
        transaction,
        (
            "symbol",
            "underlyingSymbol",
            "underlying_symbol",
            "tickerSymbol",
            "securitySymbol",
            "instrumentSymbol",
        ),
    )
    symbol = str(raw_value or "").strip().upper()
    if symbol:
        return symbol
    description = str(
        _transaction_value(
            transaction,
            (
                "description",
                "transactionDescription",
                "activityDescription",
                "securityDescription",
            ),
        )
        or ""
    ).strip()
    if description:
        matches = re.findall(r"\b[A-Z]{1,6}\b", description.upper())
        for match in matches:
            if match not in {"BUY", "SELL", "TRADE", "OPEN", "CLOSE", "LONG", "SHORT", "MARGIN", "CASH", "USD"}:
                return match.upper()
    return ""


def _transaction_quantity(transaction: dict[str, Any]) -> float:
    raw_value = _transaction_value(
        transaction,
        (
            "quantity",
            "filledQuantity",
            "orderQuantity",
            "shares",
            "longQuantity",
            "shortQuantity",
        ),
    )
    try:
        return abs(float(raw_value or 0))
    except (TypeError, ValueError):
        return 0.0


def _transaction_price(transaction: dict[str, Any]) -> float | None:
    for key in ("price", "filledPrice", "averagePrice", "pricePerShare", "netPrice", "executionPrice", "tradePrice", "limitPrice", "stopPrice"):
        raw_value = _transaction_value(transaction, (key,))
        try:
            if raw_value is not None:
                numeric = float(raw_value)
                if numeric > 0:
                    return numeric
        except (TypeError, ValueError):
            continue

    amount_value = _transaction_value(transaction, ("amount", "netAmount", "totalAmount"))
    quantity_value = _transaction_quantity(transaction)
    try:
        if amount_value is not None and quantity_value > 0:
            numeric = abs(float(amount_value)) / quantity_value
            if numeric > 0:
                return numeric
    except (TypeError, ValueError):
        return None
    return None


def _transaction_side(transaction: dict[str, Any]) -> str:
    raw_value = _transaction_value(
        transaction,
        (
            "instruction",
            "side",
            "transactionType",
            "activityType",
            "description",
        ),
    )
    text = str(raw_value or "").strip().upper()
    if "BUY" in text:
        return "BUY"
    if "SELL" in text:
        return "SELL"
    if "DIV" in text:
        return "DIV"
    return text


def _build_position_id_symbol_map(positions: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for position in positions:
        raw_position = {}
        symbol = ""
        if isinstance(position, dict):
            symbol = str(position.get("symbol") or "").strip().upper()
            metadata = dict(position.get("metadata") or {})
            raw_position = dict(metadata.get("raw") or {})
        else:
            symbol = str(position.ticker.symbol or "").strip().upper()
            raw_position = dict((position.metadata or {}).get("raw") or {})
        if not symbol:
            continue
        for key in ("positionId", "position_id"):
            raw_value = raw_position.get(key)
            if raw_value is not None:
                mapping[str(raw_value)] = symbol
    if mapping:
        logger.info(
            "finance position id map symbols=%s ids=%s",
            ",".join(sorted(set(mapping.values())))[:200],
            ",".join(sorted(mapping.keys()))[:200],
        )
    else:
        logger.info("finance position id map empty")
    return mapping


def _normalize_trade_history_rows(transactions: list[Any], position_symbol_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    position_symbol_map = position_symbol_map or {}
    for transaction in transactions:
        if not isinstance(transaction, dict):
            continue
        symbol = _transaction_symbol(transaction)
        if not symbol:
            position_id = transaction.get("positionId") or transaction.get("position_id")
            if position_id is not None:
                symbol = str(position_symbol_map.get(str(position_id)) or "").strip().upper()
        timestamp = _transaction_timestamp(transaction)
        price = _transaction_price(transaction)
        quantity = _transaction_quantity(transaction)
        if not symbol or not timestamp:
            continue
        rows.append(
            {
                "symbol": symbol,
                "timestamp": timestamp,
                "price": price,
                "quantity": quantity,
                "side": _transaction_side(transaction),
                "description": str(_transaction_value(transaction, ("description", "transactionDescription", "activityDescription")) or "").strip(),
                "transaction_id": str(_transaction_value(transaction, ("transactionId", "id", "transaction_id")) or "").strip(),
                "raw": transaction,
            }
        )
    if not rows and transactions:
        sample = transactions[0] if isinstance(transactions[0], dict) else {}
        sample_keys = sorted(sample.keys())[:40]
        nested_keys = []
        for key in ("transactionItem", "instrument", "transferItems", "orderId", "transactionId", "orderLegCollection", "orderActivityCollection", "executionLegs"):
            value = sample.get(key)
            if isinstance(value, dict):
                nested_keys.append(f"{key}:{','.join(sorted(value.keys())[:20])}")
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                nested_keys.append(f"{key}[0]:{','.join(sorted(value[0].keys())[:20])}")
        logger.info(
            "finance trade history sample dropped sample_keys=%s nested_keys=%s sample=%s",
            ",".join(sample_keys),
            ";".join(nested_keys),
            str(sample)[:1000],
        )
    rows.sort(key=lambda row: row.get("timestamp") or "")
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            seen_symbols.add(symbol)
    logger.info(
        "finance normalized trade history rows count=%s symbols=%s",
        len(rows),
        ",".join(sorted(seen_symbols))[:200],
    )
    return rows


def _quote_cache_key(symbol: str) -> str:
    return f"quote:{symbol}:latest"


def _upsert_quote_cache(workspace, ticker: Ticker, quote_payload: dict[str, Any]) -> FinanceDataCacheEntry:
    now = timezone.now()
    expires_at = now + timedelta(seconds=getattr(settings, "FINANCE_QUOTE_TTL_SECONDS", 120))
    quote_timestamp = quote_payload.get("timestamp")
    if isinstance(quote_timestamp, str):
        parsed_timestamp = parse_datetime(quote_timestamp)
        if parsed_timestamp is not None:
            quote_timestamp = parsed_timestamp
        else:
            quote_timestamp = now
    if quote_timestamp is None:
        quote_timestamp = now
    entry, _ = FinanceDataCacheEntry.objects.update_or_create(
        cache_key=_quote_cache_key(ticker.symbol),
        defaults={
            "workspace": workspace,
            "ticker": ticker,
            "data_kind": FinanceDataCacheEntry.DataKind.QUOTE,
            "source_name": str(quote_payload.get("source") or "finance"),
            "timeframe": "",
            "as_of": quote_timestamp,
            "expires_at": expires_at,
            "payload": quote_payload,
            "summary_text": f"{ticker.symbol} quote cached for finance bootstrap.",
            "response_hash": "",
            "metadata": {"bootstrap": True, "ttl_seconds": getattr(settings, "FINANCE_QUOTE_TTL_SECONDS", 120)},
        },
    )
    entry.refresh_from_db()
    logger.info(
        "finance quote cache upserted symbol=%s as_of=%s expires_at=%s ttl_seconds=%s",
        ticker.symbol,
        entry.as_of.isoformat() if entry.as_of else "",
        entry.expires_at.isoformat() if entry.expires_at else "",
        getattr(settings, "FINANCE_QUOTE_TTL_SECONDS", 120),
    )
    return entry


def _collect_market_symbols(portfolio: Portfolio, watchlist: Watchlist) -> list[str]:
    positions = list(portfolio.positions.select_related("ticker").order_by("ticker__symbol"))
    watchlist_items = list(watchlist.items.select_related("ticker").order_by("ticker__symbol"))
    symbols: list[str] = []
    seen: set[str] = set()
    for position in positions:
        symbol = position.ticker.symbol
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for item in watchlist_items:
        symbol = item.ticker.symbol
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _collect_position_symbols(positions: list[Any]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for position in positions:
        if isinstance(position, dict):
            symbol = str(position.get("symbol") or "").strip().upper()
        else:
            symbol = str(getattr(getattr(position, "ticker", None), "symbol", "") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _history_payload_has_bars(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").strip().lower() == "unavailable":
        return False
    bars = payload.get("bars") or payload.get("candles") or []
    return isinstance(bars, list) and bool(bars)


def bootstrap_finance_workspace(
    *,
    workspace,
    owner,
    refresh_quotes: bool = False,
    refresh_brokerage: bool = False,
    live_refresh: bool = True,
) -> dict[str, Any]:
    now = timezone.now()
    portfolio = _find_default_portfolio(workspace, owner)
    watchlist = _find_default_watchlist(workspace, owner)

    broker_snapshot: dict[str, Any] = {}
    positions_snapshot: dict[str, Any] = {}
    transactions_snapshot: dict[str, Any] = {}
    orders_snapshot: dict[str, Any] = {}
    primary_account_hash = str((portfolio.metadata or {}).get("schwab_account_hash") or "").strip()

    brokerage_refresh_due = _portfolio_brokerage_refresh_due(portfolio)
    if live_refresh and (refresh_brokerage or brokerage_refresh_due):
        providers = build_default_providers(workspace=workspace, owner=owner)
        broker = providers["brokerage"]
        broker_snapshot = broker.list_accounts()
        primary_account_hash = str(broker_snapshot.get("primary_account_hash") or "").strip()
        if primary_account_hash:
            balances_snapshot = broker.get_balances(account_id=primary_account_hash)
            positions_snapshot = broker.list_positions(account_id=primary_account_hash)
            logger.info(
                "finance bootstrap transaction fetch started workspace_id=%s owner_id=%s account_hash=%s lookback_days=%s",
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
                "finance bootstrap transaction fetch finished workspace_id=%s owner_id=%s account_hash=%s status=%s count=%s",
                workspace.id,
                owner.id,
                primary_account_hash,
                transactions_snapshot.get("status") if isinstance(transactions_snapshot, dict) else "",
                len((transactions_snapshot.get("transactions") or []) if isinstance(transactions_snapshot, dict) else []),
            )
            logger.info(
                "finance bootstrap order fetch started workspace_id=%s owner_id=%s account_hash=%s lookback_days=%s",
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
                "finance bootstrap order fetch finished workspace_id=%s owner_id=%s account_hash=%s status=%s count=%s",
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
            existing_metadata = dict(portfolio.metadata or {})
            portfolio.metadata = {
                **existing_metadata,
                "schwab_account_hash": primary_account_hash,
                "schwab_accounts": broker_snapshot.get("accounts") or [],
                "cash": float(cash_available or 0),
                "market_value": float((positions_snapshot.get("raw") or {}).get("marketValue") or existing_metadata.get("market_value") or 0),
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
                    cost_basis = float(average_price * abs(quantity))
                    side = Position.Side.LONG if quantity >= 0 else Position.Side.SHORT
                    seen_position_keys.add((symbol, side))
                    existing_position = Position.objects.filter(portfolio=portfolio, ticker=ticker, side=side).first()
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
            deleted_count = 0
            for stale_position in Position.objects.filter(portfolio=portfolio).select_related("ticker"):
                stale_symbol = str(getattr(getattr(stale_position, "ticker", None), "symbol", "") or "").strip().upper()
                stale_side = str(stale_position.side or "").strip().upper()
                if (stale_symbol, stale_side) in seen_position_keys:
                    continue
                stale_position.delete()
                deleted_count += 1
            if deleted_count:
                logger.info(
                    "finance brokerage positions removed workspace_id=%s owner_id=%s removed=%s",
                    workspace.id,
                    owner.id,
                    deleted_count,
                )

    portfolio = _find_default_portfolio(workspace, owner)
    watchlist = _find_default_watchlist(workspace, owner)
    positions = list(portfolio.positions.select_related("ticker").order_by("ticker__symbol"))
    watchlist_items = list(watchlist.items.select_related("ticker").order_by("ticker__symbol"))
    symbols = _collect_market_symbols(portfolio, watchlist)
    position_symbols = _collect_position_symbols(positions)
    snapshot_key = f"workspace:{workspace.id}:finance-bootstrap"
    existing_snapshot = FinanceResearchSnapshot.objects.filter(snapshot_key=snapshot_key).first()
    existing_payload = existing_snapshot.payload if existing_snapshot and isinstance(existing_snapshot.payload, dict) else {}
    existing_history_map = dict(existing_payload.get("price_history_map") or {})
    transactions = list((portfolio.metadata or {}).get("schwab_transactions") or [])
    orders = list((portfolio.metadata or {}).get("schwab_orders") or [])
    trade_history_rows = _normalize_trade_history_rows(transactions, _build_position_id_symbol_map(positions))
    order_history_rows = _normalize_trade_history_rows(orders, _build_position_id_symbol_map(positions))
    if order_history_rows:
        trade_history_rows = order_history_rows

    quotes: list[dict[str, Any]] = []
    quote_status = "cached"
    market_data = None
    market_data_backup = None
    primary_quote_map: dict[str, dict[str, Any]] = {}
    backup_quote_map: dict[str, dict[str, Any]] = {}
    price_history_map: dict[str, dict[str, Any]] = {
        symbol: dict(existing_history_map.get(symbol) or {})
        for symbol in position_symbols
        if isinstance(existing_history_map.get(symbol), dict)
    }
    had_misses = False
    refreshed_any = False
    if live_refresh and refresh_quotes:
        providers = build_default_providers(workspace=workspace, owner=owner)
        market_data = providers["market_data"]
        market_data_backup = providers.get("market_data_backup")
        if symbols:
            try:
                primary_quote_map = market_data.get_quotes(symbols)
            except NotImplementedError:
                primary_quote_map = {}
            except Exception as exc:
                logger.warning(
                    "finance bootstrap quote batch failed workspace_id=%s owner_id=%s provider=%s error=%s",
                    workspace.id,
                    owner.id,
                    getattr(market_data, "provider_name", "market_data"),
                    exc,
                )
                primary_quote_map = {}
            missing_symbols = [
                symbol
                for symbol in symbols
                if _extract_quote_last_price(primary_quote_map.get(symbol)) is None
            ]
            if missing_symbols and market_data_backup is not None:
                try:
                    backup_quote_map = market_data_backup.get_quotes(missing_symbols)
                except NotImplementedError:
                    backup_quote_map = {}
                except Exception as exc:
                    logger.warning(
                        "finance bootstrap quote backup batch failed workspace_id=%s owner_id=%s provider=%s error=%s",
                        workspace.id,
                        owner.id,
                        getattr(market_data_backup, "provider_name", "market_data_backup"),
                        exc,
                    )
                    backup_quote_map = {}
        if position_symbols:
            history_start = now - timedelta(days=30)
            for symbol in position_symbols:
                history_payload: dict[str, Any] | None = None
                for provider in (market_data, market_data_backup):
                    if provider is None:
                        continue
                    try:
                        candidate = provider.get_history(symbol, timeframe="daily", start=history_start, end=now)
                    except NotImplementedError:
                        continue
                    except Exception as exc:
                        logger.warning(
                            "finance bootstrap price history failed workspace_id=%s owner_id=%s symbol=%s provider=%s error=%s",
                            workspace.id,
                            owner.id,
                            symbol,
                            getattr(provider, "provider_name", "market_data"),
                            exc,
                        )
                        continue
                    if _history_payload_has_bars(candidate):
                        history_payload = candidate
                        break
                if history_payload is not None:
                    price_history_map[symbol] = history_payload

    ticker_map = {ticker.symbol.upper(): ticker for ticker in Ticker.objects.filter(symbol__in=symbols)}

    for symbol in symbols:
        ticker = ticker_map.get(symbol.upper())
        if ticker is None:
            continue
        cached_quote = (
            FinanceDataCacheEntry.objects.filter(
                cache_key=_quote_cache_key(symbol),
                data_kind=FinanceDataCacheEntry.DataKind.QUOTE,
            )
            .order_by("-as_of", "-created_at")
            .first()
        )
        quote_payload: dict[str, Any] | None = None
        if cached_quote and (cached_quote.expires_at is None or cached_quote.expires_at > now):
            quote_payload = dict(cached_quote.payload or {})
            quote_status = "cached"
        elif refresh_quotes:
            candidate = primary_quote_map.get(symbol) if primary_quote_map else None
            if _extract_quote_last_price(candidate) is not None:
                quote_payload = dict(candidate or {})
            else:
                candidate = backup_quote_map.get(symbol) if backup_quote_map else None
                if _extract_quote_last_price(candidate) is not None:
                    quote_payload = dict(candidate or {})
            if quote_payload is not None:
                cached_quote = _upsert_quote_cache(workspace, ticker, quote_payload)
                quote_status = "refreshed"
                refreshed_any = True
            elif cached_quote:
                quote_payload = dict(cached_quote.payload or {})
                quote_status = "partial"
            else:
                had_misses = True
        elif cached_quote:
            quote_payload = dict(cached_quote.payload or {})
            quote_status = "stale"

        if quote_payload is None:
            continue
        quotes.append(
            {
                "symbol": symbol,
                "payload": quote_payload,
                "cache_key": _quote_cache_key(symbol),
                "as_of": cached_quote.as_of.isoformat() if cached_quote and cached_quote.as_of else "",
            }
        )

    if refresh_quotes:
        if refreshed_any:
            quote_status = "partial" if had_misses else "refreshed"
        elif had_misses:
            quote_status = "failed"
        else:
            quote_status = "cached"

    positions_payload = [_serialize_position(position) for position in positions]
    quote_map = {
        str(entry.get("symbol") or "").strip().upper(): {
            "payload": dict(entry.get("payload") or {}),
            "as_of": str(entry.get("as_of") or "").strip(),
        }
        for entry in quotes
        if isinstance(entry, dict)
    }
    position_rows = _build_position_rows(positions, quote_map)
    price_history_rows = [
        {
            "symbol": symbol,
            "payload": dict(payload or {}),
            "cache_key": f"price_history:{symbol}:30d",
        }
        for symbol, payload in sorted(price_history_map.items())
        if isinstance(payload, dict)
    ]
    watchlist_payload = [
        {
            "symbol": item.ticker.symbol,
            "name": item.ticker.name,
            "note": item.note,
            "added_at": item.created_at.isoformat(),
        }
        for item in watchlist_items
    ]

    snapshot_payload = {
        "portfolio": {
            "portfolio_id": str(portfolio.id),
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "positions": positions_payload,
            "cash": float((portfolio.metadata or {}).get("cash") or 0),
        },
        "watchlist": {
            "watchlist_id": str(watchlist.id),
            "watchlist_name": watchlist.name,
            "items": watchlist_payload,
        },
        "quotes": quotes,
        "source_policy": {
            "positions_ttl": "infinite",
            "quotes_ttl_seconds": getattr(settings, "FINANCE_QUOTE_TTL_SECONDS", 120),
            "brokerage_ttl_seconds": getattr(settings, "FINANCE_BROKERAGE_REFRESH_TTL_SECONDS", 600),
            "research_snapshot_ttl_seconds": getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 300),
            "transactions_ttl_seconds": getattr(settings, "FINANCE_BROKERAGE_REFRESH_TTL_SECONDS", 600),
            "auto_fetch_enabled": getattr(settings, "FINANCE_AUTO_FETCH_ENABLED", True),
        },
        "position_rows": position_rows,
        "price_history_rows": price_history_rows,
        "price_history_map": price_history_map,
        "transactions": transactions,
        "orders": orders,
        "trade_history_rows": trade_history_rows,
        "order_history_rows": order_history_rows,
    }

    snapshot, _ = FinanceResearchSnapshot.objects.update_or_create(
        snapshot_key=snapshot_key,
        defaults={
            "workspace": workspace,
            "portfolio": portfolio,
            "watchlist": watchlist,
            "ticker": None,
            "snapshot_kind": FinanceResearchSnapshot.SnapshotKind.OTHER,
            "timeframe": "daily",
            "as_of": now,
            "expires_at": now + timedelta(seconds=getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 300)),
            "summary_text": f"Finance bootstrap snapshot with {len(positions_payload)} positions and {len(quotes)} quotes.",
            "payload": snapshot_payload,
            "source_keys": [entry["cache_key"] for entry in quotes],
            "metadata": {
                "bootstrap": True,
                "quote_status": quote_status,
                "position_ttl": "infinite",
            },
        },
    )

    return {
        "snapshot_key": snapshot.snapshot_key,
        "snapshot_id": str(snapshot.id),
        "snapshot_kind": snapshot.snapshot_kind,
        "workspace_name": getattr(workspace, "name", ""),
        "as_of": snapshot.as_of.isoformat() if snapshot.as_of else "",
        "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else "",
        "portfolio": {
            "portfolio_id": str(portfolio.id),
            "name": portfolio.name,
            "position_count": len(positions_payload),
            "cash": float((portfolio.metadata or {}).get("cash") or 0),
        },
        "watchlist": {
            "watchlist_id": str(watchlist.id),
            "name": watchlist.name,
            "count": len(watchlist_payload),
        },
        "quotes": quotes,
        "positions": positions_payload,
        "position_rows": position_rows,
        "price_history_rows": price_history_rows,
        "price_history_map": price_history_map,
        "transactions": transactions,
        "orders": orders,
        "trade_history_rows": trade_history_rows,
        "order_history_rows": order_history_rows,
        "source_policy": snapshot_payload["source_policy"],
        "quote_status": quote_status,
    }
