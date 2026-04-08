from __future__ import annotations

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model

from core.models import Workspace
from logging_utils import get_app_logger
from finance.models import Portfolio
from finance.services.refresh import (
    refresh_brokerage_snapshot,
    refresh_expired_quotes,
    refresh_finance_snapshot,
    refresh_finance_symbol_batch,
    refresh_finance_workspace,
)
from finance.services.ticker_universe import refresh_ticker_history, refresh_ticker_universe


logger = get_app_logger("finance")


def _iter_finance_targets():
    seen: set[tuple[str, str]] = set()
    portfolios = (
        Portfolio.objects.select_related("workspace", "owner")
        .filter(workspace__is_active=True, owner__isnull=False)
        .order_by("workspace_id", "owner_id", "-is_default", "created_at")
    )
    for portfolio in portfolios:
        if portfolio.workspace_id is None or portfolio.owner_id is None:
            continue
        key = (str(portfolio.workspace_id), str(portfolio.owner_id))
        if key in seen:
            continue
        seen.add(key)
        yield portfolio.workspace, portfolio.owner


@shared_task(name="finance.tasks.prefetch_finance_workspace")
def prefetch_finance_workspace(workspace_id: str, owner_id: str) -> dict[str, object]:
    logger.info("finance prefetch started workspace_id=%s owner_id=%s", workspace_id, owner_id)
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    refresh_result = refresh_finance_workspace(workspace=workspace, owner=owner, force=True)
    logger.info(
        "finance prefetch finished workspace_id=%s owner_id=%s quote_status=%s quote_count=%s position_count=%s brokerage_refreshed=%s",
        workspace.id,
        owner_id,
        refresh_result.get("quote_status") or "unknown",
        refresh_result.get("quote_count") or 0,
        refresh_result.get("position_count") or 0,
        bool(refresh_result.get("brokerage_refreshed")),
    )
    return {
        "ok": True,
        "workspace_id": str(workspace.id),
        "owner_id": str(owner_id),
        "quote_status": refresh_result.get("quote_status") or "unknown",
        "quote_count": refresh_result.get("quote_count") or 0,
        "position_count": refresh_result.get("position_count") or 0,
        "auto_fetch_enabled": getattr(settings, "FINANCE_AUTO_FETCH_ENABLED", True),
        "brokerage_refreshed": bool(refresh_result.get("brokerage_refreshed")),
    }


@shared_task(name="finance.tasks.refresh_expired_quotes")
def refresh_expired_quotes_task(workspace_id: str, owner_id: str, force: bool = False) -> dict[str, object]:
    logger.info(
        "finance quote refresh started workspace_id=%s owner_id=%s force=%s",
        workspace_id,
        owner_id,
        force,
    )
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_expired_quotes(workspace=workspace, owner=owner, force=force)
    logger.info(
        "finance quote refresh finished workspace_id=%s owner_id=%s quote_count=%s refreshed=%s",
        workspace.id,
        owner_id,
        result.get("quote_count") or 0,
        len(result.get("refreshed_symbols") or []),
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_brokerage_snapshot")
def refresh_brokerage_snapshot_task(workspace_id: str, owner_id: str, force: bool = False) -> dict[str, object]:
    logger.info(
        "finance brokerage refresh task started workspace_id=%s owner_id=%s force=%s",
        workspace_id,
        owner_id,
        force,
    )
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_brokerage_snapshot(workspace=workspace, owner=owner, force=force)
    logger.info(
        "finance brokerage refresh task finished workspace_id=%s owner_id=%s refreshed=%s position_count=%s",
        workspace.id,
        owner_id,
        bool(result.get("refreshed")),
        result.get("position_count") or 0,
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_finance_snapshot")
def refresh_finance_snapshot_task(workspace_id: str, owner_id: str) -> dict[str, object]:
    logger.info("finance snapshot refresh started workspace_id=%s owner_id=%s", workspace_id, owner_id)
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_finance_snapshot(workspace=workspace, owner=owner)
    logger.info(
        "finance snapshot refresh finished workspace_id=%s owner_id=%s quote_count=%s position_count=%s",
        workspace.id,
        owner_id,
        result.get("quote_count") or 0,
        result.get("position_count") or 0,
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_finance_symbol_batch")
def refresh_finance_symbol_batch_task(workspace_id: str, owner_id: str, symbols: list[str] | None = None, force: bool = False) -> dict[str, object]:
    logger.info(
        "finance symbol batch refresh started workspace_id=%s owner_id=%s force=%s symbol_count=%s",
        workspace_id,
        owner_id,
        force,
        len(symbols or []),
    )
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_finance_symbol_batch(workspace=workspace, owner=owner, symbols=list(symbols or []), force=force)
    logger.info(
        "finance symbol batch refresh finished workspace_id=%s owner_id=%s refreshed=%s quote_count=%s",
        workspace.id,
        owner_id,
        len(result.get("refreshed_symbols") or []),
        result.get("quote_count") or 0,
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_ticker_research_quote")
def refresh_ticker_research_quote_task(workspace_id: str, owner_id: str, symbol: str, force: bool = False) -> dict[str, object]:
    logger.info(
        "finance research quote task started workspace_id=%s owner_id=%s symbol=%s force=%s",
        workspace_id,
        owner_id,
        symbol,
        force,
    )
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_finance_symbol_batch(workspace=workspace, owner=owner, symbols=[symbol], force=force, rebuild_snapshot=False)
    logger.info(
        "finance research quote task finished workspace_id=%s owner_id=%s refreshed=%s quote_status=%s",
        workspace.id,
        owner_id,
        ",".join(result.get("refreshed_symbols") or []) or "-",
        result.get("quote_status") or "cached",
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_ticker_research_history")
def refresh_ticker_research_history_task(workspace_id: str, owner_id: str, symbol: str, days: int = 250) -> dict[str, object]:
    logger.info(
        "finance research history task started workspace_id=%s owner_id=%s symbol=%s days=%s",
        workspace_id,
        owner_id,
        symbol,
        days,
    )
    workspace = Workspace.objects.filter(id=workspace_id).first()
    if workspace is None:
        return {"ok": False, "status": "missing_workspace", "workspace_id": str(workspace_id)}
    owner = get_user_model().objects.filter(id=owner_id).first()
    if owner is None:
        return {"ok": False, "status": "missing_owner", "owner_id": str(owner_id), "workspace_id": str(workspace_id)}
    result = refresh_ticker_history(workspace=workspace, owner=owner, symbol=symbol, days=days)
    logger.info(
        "finance research history task finished workspace_id=%s owner_id=%s symbol=%s refreshed=%s bar_count=%s",
        workspace.id,
        owner_id,
        symbol,
        bool(result.get("refreshed")),
        result.get("bar_count") or 0,
    )
    return {"ok": True, **result}


@shared_task(name="finance.tasks.refresh_expired_quotes_sweep")
def refresh_expired_quotes_sweep_task(force: bool = False) -> dict[str, object]:
    total_refreshed = 0
    total_targets = 0
    for workspace, owner in _iter_finance_targets():
        total_targets += 1
        result = refresh_expired_quotes(workspace=workspace, owner=owner, force=force)
        total_refreshed += len(result.get("refreshed_symbols") or [])
    logger.info(
        "finance quote sweep finished targets=%s refreshed=%s force=%s",
        total_targets,
        total_refreshed,
        force,
    )
    return {"ok": True, "targets": total_targets, "refreshed_symbols": total_refreshed, "force": force}


@shared_task(name="finance.tasks.refresh_brokerage_snapshot_sweep")
def refresh_brokerage_snapshot_sweep_task(force: bool = False) -> dict[str, object]:
    total_targets = 0
    refreshed_targets = 0
    for workspace, owner in _iter_finance_targets():
        total_targets += 1
        result = refresh_brokerage_snapshot(workspace=workspace, owner=owner, force=force)
        if result.get("refreshed"):
            refreshed_targets += 1
    logger.info(
        "finance brokerage sweep finished targets=%s refreshed=%s force=%s",
        total_targets,
        refreshed_targets,
        force,
    )
    return {"ok": True, "targets": total_targets, "refreshed_targets": refreshed_targets, "force": force}


@shared_task(name="finance.tasks.refresh_finance_snapshot_sweep")
def refresh_finance_snapshot_sweep_task() -> dict[str, object]:
    total_targets = 0
    refreshed_targets = 0
    for workspace, owner in _iter_finance_targets():
        total_targets += 1
        result = refresh_finance_snapshot(workspace=workspace, owner=owner, force=False)
        if result.get("refreshed"):
            refreshed_targets += 1
    logger.info(
        "finance snapshot sweep finished targets=%s refreshed=%s",
        total_targets,
        refreshed_targets,
    )
    return {"ok": True, "targets": total_targets, "refreshed_targets": refreshed_targets}


@shared_task(name="finance.tasks.refresh_ticker_universe")
def refresh_ticker_universe_task() -> dict[str, object]:
    logger.info("finance ticker universe refresh started")
    result = refresh_ticker_universe()
    logger.info(
        "finance ticker universe refresh finished pages=%s rows=%s upserted=%s finished=%s",
        result.get("pages") or 0,
        result.get("rows") or 0,
        result.get("upserted") or 0,
        bool(result.get("finished")),
    )
    return {"ok": True, **result}
