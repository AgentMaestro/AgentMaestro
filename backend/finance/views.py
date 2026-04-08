from core.models import Workspace, WorkspaceMembership
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from logging_utils import get_app_logger

from finance.services import bootstrap_finance_workspace, build_finance_system_context_overlay
from finance.services.ticker_universe import build_ticker_lookup_context, build_ticker_research_context, search_ticker_universe
from finance.providers.schwab import (
    build_schwab_authorize_url,
    build_schwab_market_authorize_url,
    exchange_schwab_authorization_code,
    exchange_schwab_market_authorization_code,
    store_schwab_credential,
)
from finance.tasks import (
    prefetch_finance_workspace,
    refresh_ticker_research_history_task,
    refresh_ticker_research_quote_task,
)


logger = get_app_logger("finance")


def _get_or_create_finance_workspace(user):
    membership = (
        WorkspaceMembership.objects.select_related("workspace")
        .filter(user=user, is_active=True)
        .annotate(
            role_priority=Case(
                When(role=WorkspaceMembership.Role.OWNER, then=Value(0)),
                When(role=WorkspaceMembership.Role.ADMIN, then=Value(1)),
                When(role=WorkspaceMembership.Role.OPERATOR, then=Value(2)),
                When(role=WorkspaceMembership.Role.VIEWER, then=Value(3)),
                default=Value(99),
                output_field=IntegerField(),
            )
        )
        .order_by("role_priority", "-created_at")
        .first()
    )
    if membership is not None:
        return membership.workspace
    workspace, _ = Workspace.objects.get_or_create(
        name="Finance Workspace",
        defaults={"is_active": True},
    )
    WorkspaceMembership.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={"role": WorkspaceMembership.Role.OWNER},
    )
    return workspace


@login_required
@ensure_csrf_cookie
def research_home(request):
    workspace = _get_or_create_finance_workspace(request.user)
    bootstrap = bootstrap_finance_workspace(workspace=workspace, owner=request.user, refresh_quotes=False, refresh_brokerage=False, live_refresh=False)
    finance_context = build_finance_system_context_overlay(bootstrap, include_watchlist=False)
    finance_agent_slug = getattr(settings, "FINANCE_AGENT_SLUG", "") or ""
    context = {
        "page_title": "Finance Research",
        "starter_timeframes": ["Daily", "Weekly"],
        "starter_indicators": ["Moving Averages", "Stochastics", "ATR", "Volume Trends"],
        "pricing_models": ["Black-Scholes", "Binomial Tree"],
        "finance_bootstrap": bootstrap,
        "finance_workspace_name": workspace.name,
        "finance_system_context": finance_context,
        "finance_agent_slug": finance_agent_slug,
        "finance_refresh_url": "/finance/refresh/",
        "finance_auto_fetch_default": getattr(settings, "FINANCE_AUTO_FETCH_ENABLED", True),
        "finance_quote_ttl_seconds": max(1, int(getattr(settings, "FINANCE_QUOTE_TTL_SECONDS", 120))),
        "finance_brokerage_ttl_seconds": max(1, int(getattr(settings, "FINANCE_BROKERAGE_REFRESH_TTL_SECONDS", 600))),
        "finance_research_ttl_seconds": max(1, int(getattr(settings, "FINANCE_RESEARCH_SNAPSHOT_TTL_SECONDS", 86400))),
    }
    return render(request, "finance/research_home.html", context)


@login_required
@require_http_methods(["POST"])
def refresh_finance(request):
    workspace = _get_or_create_finance_workspace(request.user)
    task = prefetch_finance_workspace.delay(str(workspace.id), str(request.user.id))
    logger.info(
        "queued finance refresh workspace_id=%s owner_id=%s task_id=%s",
        workspace.id,
        request.user.id,
        task.id,
    )
    return JsonResponse(
        {
            "ok": True,
            "queued": True,
            "task_id": task.id,
            "workspace_id": str(workspace.id),
        }
    )


@login_required
@require_http_methods(["GET"])
def finance_state(request):
    workspace = _get_or_create_finance_workspace(request.user)
    bootstrap = bootstrap_finance_workspace(workspace=workspace, owner=request.user, refresh_quotes=False, refresh_brokerage=False, live_refresh=False)
    return JsonResponse({"ok": True, "bootstrap": bootstrap})


@login_required
@require_http_methods(["GET"])
def ticker_search(request):
    workspace = _get_or_create_finance_workspace(request.user)
    query = str(request.GET.get("query") or request.GET.get("q") or "").strip()
    symbol = str(request.GET.get("symbol") or "").strip().upper()
    try:
        limit = int(request.GET.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = min(max(limit, 1), 10)
    matches = search_ticker_universe(query, limit=limit) if query else []
    selected = build_ticker_lookup_context(symbol) if symbol else None
    if selected is None and matches:
        selected = build_ticker_lookup_context(str(matches[0].get("symbol") or "").strip())
    return JsonResponse(
        {
            "ok": True,
            "workspace_id": str(workspace.id),
            "query": query,
            "symbol": symbol,
            "count": len(matches),
            "matches": matches,
            "selected": selected,
        }
    )


@login_required
@require_http_methods(["GET"])
def ticker_research(request):
    workspace = _get_or_create_finance_workspace(request.user)
    symbol = str(request.GET.get("symbol") or request.GET.get("ticker") or "").strip().upper()
    if not symbol:
        return JsonResponse({"ok": False, "error": "symbol is required"}, status=400)
    queue_refresh = str(request.GET.get("queue") or "1").strip().lower() not in {"0", "false", "no"}
    context = build_ticker_research_context(symbol, workspace=workspace, owner=request.user)
    quote_task_id = ""
    history_task_id = ""
    if queue_refresh:
        quote_task = refresh_ticker_research_quote_task.delay(str(workspace.id), str(request.user.id), symbol, True)
        history_task = refresh_ticker_research_history_task.delay(str(workspace.id), str(request.user.id), symbol, 250)
        quote_task_id = quote_task.id
        history_task_id = history_task.id
        logger.info(
            "queued ticker research refresh workspace_id=%s owner_id=%s symbol=%s quote_task_id=%s history_task_id=%s",
            workspace.id,
            request.user.id,
            symbol,
            quote_task_id,
            history_task_id,
        )
    return JsonResponse(
        {
            "ok": True,
            "workspace_id": str(workspace.id),
            "queued": queue_refresh,
            "quote_task_id": quote_task_id,
            "history_task_id": history_task_id,
            "context": context,
        }
    )


@login_required
def schwab_connect(request):
    workspace = _get_or_create_finance_workspace(request.user)
    request.session["schwab_oauth_flow"] = "brokerage"
    request.session["schwab_oauth_workspace_id"] = str(workspace.id)
    request.session["schwab_oauth_owner_id"] = str(request.user.id)
    request.session["schwab_oauth_started_at"] = timezone.now().isoformat()
    request.session.save()
    try:
        authorize_url = build_schwab_authorize_url()
    except Exception as exc:
        logger.exception(
            "schwab connect failed workspace_id=%s owner_id=%s callback_url=%s error=%s",
            workspace.id,
            request.user.id,
            getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
            exc,
        )
        return render(
            request,
            "finance/schwab_callback.html",
            {
                "page_title": "Schwab Callback",
                "callback_url": getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
                "query_params": {},
                "connect_status": "error",
                "credential_stored": False,
                "exchange_result": {"error": str(exc)},
            },
            status=500,
        )
    logger.info(
        "schwab connect start workspace_id=%s owner_id=%s callback_url=%s authorize_url=%s",
        workspace.id,
        request.user.id,
        getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
        authorize_url.split("?", 1)[0],
    )
    return redirect(authorize_url)


@login_required
def schwab_market_connect(request):
    workspace = _get_or_create_finance_workspace(request.user)
    request.session["schwab_oauth_flow"] = "market_data"
    request.session["schwab_market_oauth_workspace_id"] = str(workspace.id)
    request.session["schwab_market_oauth_owner_id"] = str(request.user.id)
    request.session["schwab_market_oauth_started_at"] = timezone.now().isoformat()
    request.session.save()
    try:
        authorize_url = build_schwab_market_authorize_url()
    except Exception as exc:
        logger.exception(
            "schwab market connect failed workspace_id=%s owner_id=%s callback_url=%s error=%s",
            workspace.id,
            request.user.id,
            getattr(settings, "SCHWAB_MARKET_DATA_CALLBACK_URL", "") or getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
            exc,
        )
        return render(
            request,
            "finance/schwab_callback.html",
            {
                "page_title": "Schwab Market Data Callback",
                "callback_url": getattr(settings, "SCHWAB_MARKET_DATA_CALLBACK_URL", "") or getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
                "query_params": {},
                "connect_status": "error",
                "credential_stored": False,
                "exchange_result": {"error": str(exc)},
            },
            status=500,
        )
    logger.info(
        "schwab market connect start workspace_id=%s owner_id=%s callback_url=%s authorize_url=%s",
        workspace.id,
        request.user.id,
        getattr(settings, "SCHWAB_MARKET_DATA_CALLBACK_URL", "") or getattr(settings, "SCHWAB_CALLBACK_URL", "") or "",
        authorize_url.split("?", 1)[0],
    )
    return redirect(authorize_url)


def schwab_callback(request):
    query_params = {key: value for key, value in request.GET.items()}
    callback_url = getattr(settings, "SCHWAB_CALLBACK_URL", "") or ""
    oauth_flow = str(request.session.get("schwab_oauth_flow") or "").strip().lower()
    code = str(request.GET.get("code") or "").strip()
    exchange_result = {}
    stored = False
    status = "waiting"
    logger.info(
        "schwab callback received query_keys=%s has_code=%s has_state=%s callback_url=%s oauth_flow=%s",
        sorted(query_params.keys()),
        bool(code),
        bool(request.GET.get("state")),
        callback_url,
        oauth_flow,
    )
    if code:
        is_market_flow = oauth_flow == "market_data" or bool(request.session.get("schwab_market_oauth_workspace_id"))
        workspace_id = str(
            request.session.get("schwab_market_oauth_workspace_id")
            if is_market_flow
            else request.session.get("schwab_oauth_workspace_id")
            or ""
        ).strip()
        owner_id = str(
            request.session.get("schwab_market_oauth_owner_id")
            if is_market_flow
            else request.session.get("schwab_oauth_owner_id")
            or ""
        ).strip()
        try:
            if is_market_flow:
                exchange_result = exchange_schwab_market_authorization_code(code=code)
            else:
                exchange_result = exchange_schwab_authorization_code(code=code)
            workspace = Workspace.objects.filter(id=workspace_id).first() if workspace_id else None
            owner = request.user if request.user.is_authenticated else None
            if owner is None and owner_id:
                from django.contrib.auth import get_user_model

                owner = get_user_model().objects.filter(id=owner_id).first()
            if is_market_flow:
                store_schwab_credential(
                    token_payload=exchange_result,
                    workspace=workspace,
                    owner=owner,
                    source="schwab_market_oauth_callback",
                )
            else:
                store_schwab_credential(
                    token_payload=exchange_result,
                    workspace=workspace,
                    owner=owner,
                    primary_account_hash=str(exchange_result.get("primary_account_hash") or "").strip(),
                    account_hashes=list(exchange_result.get("account_hashes") or []),
                    source="schwab_oauth_callback",
                )
            task_id = ""
            if workspace is not None and owner is not None:
                task = prefetch_finance_workspace.delay(str(workspace.id), str(owner.id))
                task_id = str(task.id)
            exchange_result = {
                "token": exchange_result,
                "initial_sync_queued": bool(task_id),
                "task_id": task_id,
            }
            stored = True
            status = "connected"
            logger.info(
                "schwab callback stored credential workspace_id=%s owner_id=%s queued_prefetch=%s task_id=%s oauth_flow=%s",
                workspace.id if workspace else "",
                owner.id if owner else "",
                bool(task_id),
                task_id,
                oauth_flow,
            )
            if is_market_flow:
                for session_key in (
                    "schwab_oauth_flow",
                    "schwab_market_oauth_workspace_id",
                    "schwab_market_oauth_owner_id",
                    "schwab_market_oauth_started_at",
                ):
                    request.session.pop(session_key, None)
            else:
                for session_key in (
                    "schwab_oauth_flow",
                    "schwab_oauth_workspace_id",
                    "schwab_oauth_owner_id",
                    "schwab_oauth_started_at",
                ):
                    request.session.pop(session_key, None)
            request.session.save()
        except Exception as exc:
            logger.exception(
                "schwab callback exchange failed workspace_id=%s owner_id=%s code=%s query_keys=%s callback_url=%s oauth_flow=%s error=%s",
                workspace_id,
                owner_id,
                f"{code[:6]}...{code[-4:]}" if len(code) > 10 else ("*" * len(code) if code else ""),
                sorted(query_params.keys()),
                callback_url,
                oauth_flow,
                exc,
            )
            status = "error"
            exchange_result = {"error": str(exc)}
    return render(
        request,
        "finance/schwab_callback.html",
        {
            "page_title": "Schwab Callback",
            "callback_url": callback_url,
            "query_params": query_params,
            "connect_status": status,
            "credential_stored": stored,
            "exchange_result": exchange_result,
        },
    )


def schwab_market_callback(request):
    query_params = {key: value for key, value in request.GET.items()}
    callback_url = getattr(settings, "SCHWAB_MARKET_DATA_CALLBACK_URL", "") or getattr(settings, "SCHWAB_CALLBACK_URL", "") or ""
    code = str(request.GET.get("code") or "").strip()
    exchange_result = {}
    stored = False
    status = "waiting"
    logger.info(
        "schwab market callback received query_keys=%s has_code=%s has_state=%s callback_url=%s",
        sorted(query_params.keys()),
        bool(code),
        bool(request.GET.get("state")),
        callback_url,
    )
    if code:
        workspace_id = str(request.session.get("schwab_market_oauth_workspace_id") or "").strip()
        owner_id = str(request.session.get("schwab_market_oauth_owner_id") or "").strip()
        try:
            exchange_result = exchange_schwab_market_authorization_code(code=code)
            workspace = Workspace.objects.filter(id=workspace_id).first() if workspace_id else None
            owner = request.user if request.user.is_authenticated else None
            if owner is None and owner_id:
                from django.contrib.auth import get_user_model

                owner = get_user_model().objects.filter(id=owner_id).first()
            store_schwab_credential(
                token_payload=exchange_result,
                workspace=workspace,
                owner=owner,
                source="schwab_market_oauth_callback",
            )
            task_id = ""
            if workspace is not None and owner is not None:
                task = prefetch_finance_workspace.delay(str(workspace.id), str(owner.id))
                task_id = str(task.id)
            exchange_result = {
                "token": exchange_result,
                "initial_sync_queued": bool(task_id),
                "task_id": task_id,
            }
            stored = True
            status = "connected"
            logger.info(
                "schwab market callback stored credential workspace_id=%s owner_id=%s queued_prefetch=%s task_id=%s",
                workspace.id if workspace else "",
                owner.id if owner else "",
                bool(task_id),
                task_id,
            )
            for session_key in (
                "schwab_market_oauth_workspace_id",
                "schwab_market_oauth_owner_id",
                "schwab_market_oauth_started_at",
            ):
                request.session.pop(session_key, None)
            request.session.save()
        except Exception as exc:
            logger.exception(
                "schwab market callback exchange failed workspace_id=%s owner_id=%s code=%s query_keys=%s callback_url=%s error=%s",
                workspace_id,
                owner_id,
                f"{code[:6]}...{code[-4:]}" if len(code) > 10 else ("*" * len(code) if code else ""),
                sorted(query_params.keys()),
                callback_url,
                exc,
            )
            status = "error"
            exchange_result = {"error": str(exc)}
    return render(
        request,
        "finance/schwab_callback.html",
        {
            "page_title": "Schwab Market Data Callback",
            "callback_url": callback_url,
            "query_params": query_params,
            "connect_status": status,
            "credential_stored": stored,
            "exchange_result": exchange_result,
        },
    )
