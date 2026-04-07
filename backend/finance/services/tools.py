from __future__ import annotations

from datetime import date as date_class
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from finance.models import (
    FinanceDataCacheEntry,
    FinanceResearchSnapshot,
    Portfolio,
    Position,
    Ticker,
    Watchlist,
    WatchlistItem,
)
from finance.providers.registry import build_default_providers
from finance.services.market_hours import get_schwab_market_hours_state


def _ok(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "tool": tool_name, "result": result}


def _unavailable(tool_name: str, provider_name: str, *, detail: str = "") -> dict[str, Any]:
    message = detail or f"{provider_name} is not wired yet."
    return {
        "ok": False,
        "tool": tool_name,
        "error": message,
        "result": {
            "provider": provider_name,
            "status": "not_wired",
            "message": message,
        },
    }


def _normalize_symbol(value: object) -> str:
    candidate = str(value or "").strip().upper()
    return candidate.replace(" ", "")


def _normalize_name(value: object) -> str:
    return str(value or "").strip()


def _parse_tool_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_workspace_context(run) -> tuple[Any, Any]:
    owner = run.started_by or run.agent.owner
    workspace = run.workspace
    return workspace, owner


def _get_or_create_ticker(symbol: str, *, source_name: str = "manual") -> Ticker:
    ticker, _ = Ticker.objects.get_or_create(
        symbol=symbol,
        defaults={
            "name": "",
            "exchange": "",
            "asset_type": Ticker.AssetType.EQUITY,
            "currency": "USD",
            "is_active": True,
            "metadata": {"source_name": source_name},
        },
    )
    if source_name and not ticker.metadata.get("source_name"):
        ticker.metadata = {**ticker.metadata, "source_name": source_name}
        ticker.save(update_fields=["metadata", "updated_at"])
    return ticker


def _find_watchlist(workspace, owner, watchlist_name: str, *, create: bool = True) -> Watchlist | None:
    name = _normalize_name(watchlist_name) or "AI Watchlist"
    query = Watchlist.objects.filter(workspace=workspace, name__iexact=name)
    if owner is not None:
        owned = query.filter(owner=owner).order_by("-is_default", "created_at")
        watchlist = owned.first()
        if watchlist:
            return watchlist
    watchlist = query.order_by("-is_default", "created_at").first()
    if watchlist or not create:
        return watchlist
    return Watchlist.objects.create(
        workspace=workspace,
        owner=owner,
        name=name,
        description="AI-managed watchlist",
        is_default=name.lower() == "ai watchlist",
    )


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


def _serialize_ticker(ticker: Ticker) -> dict[str, Any]:
    return {
        "symbol": ticker.symbol,
        "name": ticker.name,
        "exchange": ticker.exchange,
        "asset_type": ticker.asset_type,
        "currency": ticker.currency,
        "is_active": ticker.is_active,
        "metadata": ticker.metadata,
    }


def _serialize_portfolio(portfolio: Portfolio) -> dict[str, Any]:
    positions = []
    market_value = 0.0
    for position in portfolio.positions.select_related("ticker").order_by("ticker__symbol"):
        position_market_value = position.metadata.get("market_value")
        if position_market_value is None:
            position_market_value = float(position.cost_basis or 0)
        market_value += float(position_market_value or 0)
        positions.append(
            {
                "symbol": position.ticker.symbol,
                "side": position.side,
                "quantity": float(position.quantity),
                "average_cost": float(position.average_cost),
                "cost_basis": float(position.cost_basis),
                "market_value": float(position_market_value or 0),
            }
        )
    cash = float(portfolio.metadata.get("cash") or 0)
    summary = {
        "market_value": market_value,
        "cash": cash,
        "day_change": float(portfolio.metadata.get("day_change") or 0),
    }
    return {
        "portfolio_id": str(portfolio.id),
        "name": portfolio.name,
        "base_currency": portfolio.base_currency,
        "summary": summary,
        "positions": positions,
        "metadata": portfolio.metadata,
    }


def _snapshot_key_for_args(args: dict[str, Any], *, kind: str) -> str:
    ticker = _normalize_symbol(args.get("ticker"))
    portfolio_id = _normalize_name(args.get("portfolio_id"))
    watchlist_name = _normalize_name(args.get("watchlist_name"))
    if ticker:
        return f"ticker:{ticker}:{kind}"
    if portfolio_id:
        return f"portfolio:{portfolio_id}:{kind}"
    if watchlist_name:
        return f"watchlist:{watchlist_name}:{kind}"
    return f"global:{kind}"


def execute_finance_tool(tool_name: str, run, args: dict[str, Any]) -> dict[str, Any]:
    workspace, owner = _resolve_workspace_context(run)
    if tool_name == "ticker_lookup":
        query = _normalize_symbol(args.get("query") or args.get("symbol") or args.get("ticker"))
        if not query:
            return _unavailable(tool_name, "finance", detail="ticker_lookup requires query.")
        ticker = _get_or_create_ticker(query, source_name="finance_lookup")
        return _ok(
            tool_name,
            {
                **_serialize_ticker(ticker),
                "source": "finance_local",
                "as_of": timezone.now().isoformat(),
            },
        )

    if tool_name in {"watchlist_add", "watchlist_remove", "watchlist_list"}:
        watchlist = _find_watchlist(workspace, owner, args.get("watchlist_name") or "AI Watchlist", create=tool_name == "watchlist_add")
        if watchlist is None:
            return _unavailable(tool_name, "finance", detail="Watchlist not found.")
        if tool_name == "watchlist_list":
            items = [
                {
                    "symbol": item.ticker.symbol,
                    "name": item.ticker.name,
                    "note": item.note,
                    "added_at": item.created_at.isoformat(),
                }
                for item in watchlist.items.select_related("ticker").order_by("ticker__symbol")
            ]
            return _ok(
                tool_name,
                {
                    "watchlist_name": watchlist.name,
                    "watchlist_id": str(watchlist.id),
                    "count": len(items),
                    "items": items,
                    "as_of": timezone.now().isoformat(),
                },
            )
        symbol = _normalize_symbol(args.get("symbol"))
        if not symbol:
            return _unavailable(tool_name, "finance", detail=f"{tool_name} requires symbol.")
        ticker = _get_or_create_ticker(symbol, source_name="watchlist")
        if tool_name == "watchlist_add":
            item, created = WatchlistItem.objects.get_or_create(
                watchlist=watchlist,
                ticker=ticker,
                defaults={
                    "note": _normalize_name(args.get("note")),
                    "added_by": owner,
                    "metadata": {"source": "chat"},
                },
            )
            if not created and args.get("note") is not None:
                item.note = _normalize_name(args.get("note"))
                item.added_by = owner
                item.save(update_fields=["note", "added_by", "updated_at"])
            return _ok(
                tool_name,
                {
                    "watchlist_name": watchlist.name,
                    "watchlist_id": str(watchlist.id),
                    "symbol": ticker.symbol,
                    "added": True,
                    "already_present": not created,
                    "ticker": _serialize_ticker(ticker),
                },
            )
        deleted, _ = WatchlistItem.objects.filter(watchlist=watchlist, ticker=ticker).delete()
        return _ok(
            tool_name,
            {
                "watchlist_name": watchlist.name,
                "watchlist_id": str(watchlist.id),
                "symbol": ticker.symbol,
                "removed": deleted > 0,
                "was_present": deleted > 0,
            },
        )

    if tool_name == "portfolio_get":
        portfolio_identifier = _normalize_name(args.get("portfolio_id"))
        if portfolio_identifier and portfolio_identifier not in {"", "current"}:
            portfolio = Portfolio.objects.filter(id=portfolio_identifier).first()
        else:
            portfolio = _find_default_portfolio(workspace, owner)
        if portfolio is None:
            return _unavailable(tool_name, "finance", detail="Portfolio not found.")
        return _ok(tool_name, _serialize_portfolio(portfolio))

    if tool_name in {"broker_accounts", "broker_balances", "broker_positions", "broker_activity"}:
        providers = build_default_providers(workspace=workspace, owner=owner)
        broker = providers["brokerage"]
        if tool_name == "broker_accounts":
            result = broker.list_accounts()
        elif tool_name == "broker_balances":
            result = broker.get_balances(account_id=args.get("account_id"))
        elif tool_name == "broker_positions":
            result = broker.list_positions(account_id=args.get("account_id"))
        else:
            result = broker.list_activity(account_id=args.get("account_id"), limit=int(args.get("limit") or 25))
        if isinstance(result, dict) and str(result.get("status") or "").strip().lower() == "unavailable":
            return _unavailable(tool_name, "schwab", detail=str(result.get("message") or "Schwab broker data is unavailable."))
        return _ok(tool_name, result)

    if tool_name == "get_market_hours":
        markets = args.get("markets")
        if isinstance(markets, list):
            requested_markets = [str(item or "").strip().lower() for item in markets if str(item or "").strip()]
        else:
            requested_markets = []
        if not requested_markets:
            requested_markets = ["equity", "option"]
        date_value = None
        raw_date = args.get("date")
        if raw_date:
            if isinstance(raw_date, str):
                try:
                    date_value = date_class.fromisoformat(raw_date)
                except ValueError:
                    date_value = None
        providers = build_default_providers(workspace=workspace, owner=owner)
        market = providers["market_data"]
        result = get_schwab_market_hours_state(market_data=market, markets=requested_markets, date_value=date_value)
        return _ok(tool_name, result)

    if tool_name in {"stock_quote", "stock_history", "stock_news", "stock_filings"}:
        symbol = _normalize_symbol(args.get("symbol"))
        if not symbol:
            provider_name = "massive" if tool_name != "stock_filings" else "sec"
            return _unavailable(tool_name, provider_name, detail=f"{tool_name} requires symbol.")
        providers = build_default_providers(workspace=workspace, owner=owner)
        market = providers["market_data"]
        fallback = providers.get("market_data_backup") if tool_name != "stock_filings" else None

        def _is_unavailable_result(value: object) -> bool:
            return isinstance(value, dict) and str(value.get("status") or "").strip().lower() == "unavailable"

        try:
            if tool_name == "stock_quote":
                result = market.get_quote(symbol)
            elif tool_name == "stock_history":
                result = market.get_history(
                    symbol,
                    timeframe=str(args.get("timeframe") or "daily"),
                    start=_parse_tool_datetime(args.get("start")),
                    end=_parse_tool_datetime(args.get("end")),
                )
            elif tool_name == "stock_news":
                result = market.get_news(symbol, limit=int(args.get("limit") or 10))
            else:
                result = providers["filings"].get_filings(
                    symbol,
                    filing_types=list(args.get("filing_types") or ["8-K", "10-K", "10-Q"]),
                    limit=int(args.get("limit") or 10),
                )
            if tool_name != "stock_filings" and _is_unavailable_result(result) and fallback is not None and fallback is not market:
                if tool_name == "stock_quote":
                    result = fallback.get_quote(symbol)
                elif tool_name == "stock_history":
                    result = fallback.get_history(
                        symbol,
                        timeframe=str(args.get("timeframe") or "daily"),
                        start=None,
                        end=None,
                    )
                elif tool_name == "stock_news":
                    result = fallback.get_news(symbol, limit=int(args.get("limit") or 10))
        except Exception as exc:
            provider_name = "sec" if tool_name == "stock_filings" else str(providers.get("market_data").provider_name or "market_data")
            if fallback is not None and fallback is not providers.get("market_data"):
                try:
                    if tool_name == "stock_quote":
                        result = fallback.get_quote(symbol)
                    elif tool_name == "stock_history":
                        result = fallback.get_history(
                            symbol,
                            timeframe=str(args.get("timeframe") or "daily"),
                            start=_parse_tool_datetime(args.get("start")),
                            end=_parse_tool_datetime(args.get("end")),
                        )
                    elif tool_name == "stock_news":
                        result = fallback.get_news(symbol, limit=int(args.get("limit") or 10))
                    else:
                        result = providers["filings"].get_filings(
                            symbol,
                            filing_types=list(args.get("filing_types") or ["8-K", "10-K", "10-Q"]),
                            limit=int(args.get("limit") or 10),
                        )
                    return _ok(tool_name, result)
                except Exception:
                    pass
            return _unavailable(tool_name, provider_name, detail=str(exc))
        return _ok(tool_name, result)

    if tool_name in {"research_snapshot_get", "research_snapshot_refresh"}:
        snapshot_key = _snapshot_key_for_args(args, kind="default")
        ticker_symbol = _normalize_symbol(args.get("ticker"))
        portfolio_identifier = _normalize_name(args.get("portfolio_id"))
        watchlist_name = _normalize_name(args.get("watchlist_name"))
        snapshot = FinanceResearchSnapshot.objects.filter(snapshot_key=snapshot_key).first()
        if tool_name == "research_snapshot_get":
            if snapshot is None:
                return _ok(
                    tool_name,
                    {
                        "snapshot_key": snapshot_key,
                        "snapshot_kind": "OTHER",
                        "summary_text": "",
                        "as_of": timezone.now().isoformat(),
                        "expires_at": "",
                        "payload": {},
                        "status": "miss",
                    },
                )
            return _ok(
                tool_name,
                {
                    "snapshot_key": snapshot.snapshot_key,
                    "snapshot_kind": snapshot.snapshot_kind,
                    "summary_text": snapshot.summary_text,
                    "as_of": snapshot.as_of.isoformat() if snapshot.as_of else "",
                    "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else "",
                    "payload": snapshot.payload,
                    "metadata": snapshot.metadata,
                    "source_keys": snapshot.source_keys,
                },
            )
        if snapshot is None:
            snapshot = FinanceResearchSnapshot.objects.create(
                snapshot_key=snapshot_key,
                workspace=workspace,
                snapshot_kind="TICKER" if ticker_symbol else "PORTFOLIO" if portfolio_identifier else "WATCHLIST" if watchlist_name else "OTHER",
                ticker=Ticker.objects.filter(symbol=ticker_symbol).first() if ticker_symbol else None,
                portfolio=Portfolio.objects.filter(id=portfolio_identifier).first() if portfolio_identifier else None,
                watchlist=Watchlist.objects.filter(workspace=workspace, name__iexact=watchlist_name).first() if watchlist_name else None,
                timeframe="daily",
                as_of=timezone.now(),
                expires_at=timezone.now() + timedelta(minutes=5),
                summary_text="Refresh queued.",
                payload={"queued": True},
                source_keys=[],
                metadata={"queued": True},
            )
        else:
            snapshot.summary_text = "Refresh queued."
            snapshot.as_of = timezone.now()
            snapshot.expires_at = timezone.now() + timedelta(minutes=5)
            snapshot.payload = {**snapshot.payload, "queued": True}
            snapshot.metadata = {**snapshot.metadata, "queued": True}
            snapshot.save(update_fields=["summary_text", "as_of", "expires_at", "payload", "metadata", "updated_at"])
        return _ok(
            tool_name,
            {
                "snapshot_key": snapshot.snapshot_key,
                "queued": True,
                "refresh_status": "queued",
            },
        )

    return _unavailable(tool_name, "finance", detail=f"Unsupported finance tool: {tool_name}.")
