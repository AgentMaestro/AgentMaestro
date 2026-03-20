from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from core.models import WorkspaceMembership

from .models import GoogleAccount
from .services.oauth import (
    GoogleOAuthError,
    build_authorization_url,
    exchange_authorization_code,
    fetch_userinfo,
)


GOOGLE_CONNECT_SESSION_KEY = "google_bridge_oauth"


def _get_agent_with_access(user, slug: str) -> Agent:
    agent = get_object_or_404(Agent.objects.select_related("workspace"), slug=slug)
    if agent.owner_id == user.id:
        return agent
    if WorkspaceMembership.objects.filter(workspace=agent.workspace, user=user, is_active=True).exists():
        return agent
    raise Http404


def _resolve_active_account(agent: Agent, user) -> GoogleAccount | None:
    return (
        GoogleAccount.objects.filter(workspace=agent.workspace, owner=user, is_active=True)
        .order_by("-updated_at")
        .first()
    )


def _list_active_accounts(agent: Agent, user):
    return list(
        GoogleAccount.objects.filter(workspace=agent.workspace, owner=user, is_active=True).order_by(
            "-last_synced_at", "-updated_at", "email", "google_subject"
        )
    )


def _build_session_payload(*, agent: Agent, state: str) -> dict[str, str]:
    return {
        "state": state,
        "agent_slug": agent.slug,
        "workspace_id": str(agent.workspace_id),
    }


def _get_session_state_map(request) -> dict[str, dict[str, str]]:
    return dict(request.session.get(GOOGLE_CONNECT_SESSION_KEY) or {})


def _store_session_state_map(request, state_map: dict[str, dict[str, str]]) -> None:
    request.session[GOOGLE_CONNECT_SESSION_KEY] = state_map
    request.session.modified = True


@login_required
@require_http_methods(["GET", "POST"])
def agent_google_connect(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    active_account = _resolve_active_account(agent, request.user)
    oauth_configured = True
    oauth_error = ""
    try:
        build_authorization_url(state="preview")
    except GoogleOAuthError as exc:
        oauth_configured = False
        oauth_error = str(exc)

    if request.method == "POST":
        if not oauth_configured:
            messages.error(request, oauth_error or "Google OAuth is not configured.")
            return redirect(reverse("google_bridge:agent_connect", kwargs={"slug": slug}))

        state = secrets.token_urlsafe(32)
        state_map = _get_session_state_map(request)
        state_map[state] = _build_session_payload(agent=agent, state=state)
        _store_session_state_map(request, state_map)
        authorize_url = build_authorization_url(state=state)
        return redirect(authorize_url)

    return render(
        request,
        "google_bridge/connect.html",
        {
            "agent": agent,
            "active_account": active_account,
            "active_accounts": _list_active_accounts(agent, request.user),
            "oauth_configured": oauth_configured,
            "oauth_error": oauth_error,
            "callback_url": reverse("google_bridge:callback"),
            "disconnect_url": reverse("google_bridge:agent_disconnect", kwargs={"slug": slug}),
            "status_url": reverse("google_bridge:agent_status", kwargs={"slug": slug}),
        },
    )


@login_required
@require_http_methods(["GET"])
def google_callback(request):
    state_map = _get_session_state_map(request)

    error = str(request.GET.get("error") or "").strip()
    if error:
        messages.error(request, f"Google authorization was cancelled: {error}")
        return redirect(_fallback_google_connect_url(request))

    state = str(request.GET.get("state") or "").strip()
    if not state or state not in state_map:
        return HttpResponseBadRequest("OAuth state mismatch.")

    session_payload = dict(state_map.get(state) or {})
    agent_slug = str(session_payload.get("agent_slug") or "").strip()
    if not agent_slug:
        return HttpResponseBadRequest("OAuth state mismatch.")

    agent = _get_agent_with_access(request.user, agent_slug)

    code = str(request.GET.get("code") or "").strip()
    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    token_payload = exchange_authorization_code(code)
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token:
        return HttpResponseBadRequest("Google authorization did not return an access token.")

    userinfo = fetch_userinfo(access_token)
    google_subject = str(userinfo.get("sub") or "").strip()
    email = str(userinfo.get("email") or "").strip()
    scopes = list(str(token_payload.get("scope") or "").split())
    token_expires_in = token_payload.get("expires_in")

    account = (
        GoogleAccount.objects.filter(workspace=agent.workspace, owner=request.user, google_subject=google_subject)
        .order_by("-updated_at")
        .first()
    )
    if account is None:
        account = GoogleAccount(workspace=agent.workspace, owner=request.user)

    account.google_subject = google_subject
    account.email = email
    account.scopes = scopes
    account.set_tokens(access_token=access_token, refresh_token=refresh_token or account.refresh_token)
    if isinstance(token_expires_in, (int, float)):
        from django.utils import timezone
        from datetime import timedelta

        account.token_expires_at = timezone.now() + timedelta(seconds=int(token_expires_in))
    account.is_active = True
    account.last_error = ""
    account.metadata = {
        "agent_slug": agent.slug,
        "userinfo": userinfo,
    }
    account.save()

    state_map.pop(state, None)
    if state_map:
        _store_session_state_map(request, state_map)
    else:
        request.session.pop(GOOGLE_CONNECT_SESSION_KEY, None)
    messages.success(request, f"Google account connected for {agent.name}.")
    return redirect(reverse("google_bridge:agent_connect", kwargs={"slug": agent.slug}))


@login_required
@require_http_methods(["POST"])
def agent_google_disconnect(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    account = _resolve_active_account(agent, request.user)
    if account is None:
        messages.info(request, "No active Google account was linked.")
        return redirect(reverse("google_bridge:agent_connect", kwargs={"slug": slug}))

    account.is_active = False
    account.clear_tokens()
    account.metadata = dict(account.metadata or {})
    account.metadata["disconnected_by"] = request.user.get_username()
    account.save(update_fields=[
        "is_active",
        "access_token_ciphertext",
        "refresh_token_ciphertext",
        "metadata",
        "updated_at",
    ])
    messages.success(request, f"Google account disconnected for {agent.name}.")
    return redirect(reverse("google_bridge:agent_connect", kwargs={"slug": slug}))


@login_required
@require_http_methods(["GET"])
def agent_google_status(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    account = _resolve_active_account(agent, request.user)
    if account is None:
        return JsonResponse(
            {
                "connected": False,
                "agent_slug": agent.slug,
                "workspace_id": str(agent.workspace_id),
                "accounts": [],
            }
        )
    active_accounts = _list_active_accounts(agent, request.user)
    return JsonResponse(
        {
            "connected": True,
            "agent_slug": agent.slug,
            "workspace_id": str(agent.workspace_id),
            "email": account.email,
            "google_subject": account.google_subject,
            "scopes": account.scopes,
            "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else "",
            "accounts": [
                {
                    "email": item.email,
                    "google_subject": item.google_subject,
                    "last_synced_at": item.last_synced_at.isoformat() if item.last_synced_at else "",
                    "is_active": item.is_active,
                }
                for item in active_accounts
            ],
        }
    )


def _fallback_google_connect_url(request) -> str:
    state_map = _get_session_state_map(request)
    if state_map:
        last_state = next(reversed(state_map.values()))
        agent_slug = str(last_state.get("agent_slug") or "").strip()
        if agent_slug:
            return reverse("google_bridge:agent_connect", kwargs={"slug": agent_slug})
    return reverse("admin:index")
