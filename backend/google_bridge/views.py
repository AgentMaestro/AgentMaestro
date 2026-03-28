from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.models import Agent
from core.models import WorkspaceMembership
from google_bridge.services.bridge import resolve_google_account, resolve_google_accounts, set_primary_google_account
from google_bridge.services.client import GoogleApiError, GoogleBridgeClient

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
    return resolve_google_account(workspace=agent.workspace, owner=user)


def _list_active_accounts(agent: Agent, user):
    return resolve_google_accounts(workspace=agent.workspace, owner=user, account_scope="all")


def _serialize_google_account(account: GoogleAccount, *, selected_id: str = "") -> dict[str, object]:
    metadata = dict(account.metadata or {})
    account_id = str(account.id)
    email = str(account.email or "").strip()
    google_subject = str(account.google_subject or "").strip()
    display_name = email or google_subject or account_id
    return {
        "id": account_id,
        "email": email,
        "google_subject": google_subject,
        "display_name": display_name,
        "is_primary": bool(metadata.get("is_primary")),
        "selected": account_id == selected_id,
        "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else "",
    }


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
            "selected_account_id": str(active_account.id) if active_account else "",
            "google_primary_account": str(getattr(settings, "GOOGLE_PRIMARY_ACCOUNT", "") or "").strip(),
            "oauth_configured": oauth_configured,
            "oauth_error": oauth_error,
            "callback_url": reverse("google_bridge:callback"),
            "disconnect_url": reverse("google_bridge:agent_disconnect", kwargs={"slug": slug}),
            "select_account_url": reverse("google_bridge:agent_account", kwargs={"slug": slug}),
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
                "selected_account_id": "",
            }
        )
    active_accounts = _list_active_accounts(agent, request.user)
    return JsonResponse(
        {
            "connected": True,
            "agent_slug": agent.slug,
            "workspace_id": str(agent.workspace_id),
            "selected_account_id": str(account.id),
            "email": account.email,
            "google_subject": account.google_subject,
            "scopes": account.scopes,
            "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else "",
            "accounts": [
                _serialize_google_account(item, selected_id=str(account.id))
                for item in active_accounts
            ],
        }
    )


@login_required
@require_http_methods(["GET"])
def agent_google_drive_browser(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    account_id = str(request.GET.get("account_id") or "").strip()
    account = resolve_google_account(workspace=agent.workspace, owner=request.user, account_id=account_id) if account_id else _resolve_active_account(agent, request.user)
    if account is None:
        account = _resolve_active_account(agent, request.user)
    connect_url = reverse("google_bridge:agent_connect", kwargs={"slug": slug})
    if account is None:
        return JsonResponse(
            {
                "connected": False,
                "agent_slug": agent.slug,
                "workspace_id": str(agent.workspace_id),
                "connect_url": connect_url,
                "files": [],
                "current_folder_id": "",
                "parent_folder_id": "",
                "accounts": [],
                "selected_account_id": "",
            }
        )

    parent_id = str(request.GET.get("parent_id") or "").strip()
    q = ["trashed=false"]
    if parent_id:
        q.append(f"'{parent_id}' in parents")
    else:
        q.append("'root' in parents")
    search_query = " and ".join(q)
    page_size = 100
    try:
        page_size = max(1, min(int(str(request.GET.get("page_size") or 100).strip() or 100), 200))
    except ValueError:
        page_size = 100

    client = GoogleBridgeClient(account)
    try:
        data = client.list_drive_files(q=search_query, page_size=page_size)
    except GoogleApiError as exc:
        return JsonResponse(
            {
                "connected": True,
                "agent_slug": agent.slug,
                "workspace_id": str(agent.workspace_id),
                "account_email": account.email,
                "google_subject": account.google_subject,
                "connect_url": connect_url,
                "current_folder_id": parent_id,
                "parent_folder_id": "",
                "files": [],
                "error": str(exc),
                "accounts": [
                    _serialize_google_account(item, selected_id=str(account.id))
                    for item in _list_active_accounts(agent, request.user)
                ],
                "selected_account_id": str(account.id),
            },
            status=502,
        )

    files = []
    for item in list(data.get("files") or []):
        file_item = dict(item)
        mime_type = str(file_item.get("mimeType") or "")
        files.append(
            {
                "id": str(file_item.get("id") or ""),
                "name": str(file_item.get("name") or ""),
                "mime_type": mime_type,
                "size_bytes": int(str(file_item.get("size") or "0") or 0) if str(file_item.get("size") or "").strip().isdigit() else 0,
                "modified_time": str(file_item.get("modifiedTime") or ""),
                "created_time": str(file_item.get("createdTime") or ""),
                "web_view_link": str(file_item.get("webViewLink") or ""),
                "web_content_link": str(file_item.get("webContentLink") or ""),
                "parents": list(file_item.get("parents") or []),
                "is_folder": mime_type == "application/vnd.google-apps.folder",
            }
        )

    return JsonResponse(
        {
            "connected": True,
            "agent_slug": agent.slug,
            "workspace_id": str(agent.workspace_id),
            "account_email": account.email,
            "google_subject": account.google_subject,
            "connect_url": connect_url,
            "current_folder_id": parent_id or "root",
            "parent_folder_id": "",
            "files": files,
            "next_page_token": str(data.get("nextPageToken") or ""),
            "accounts": [
                _serialize_google_account(item, selected_id=str(account.id))
                for item in _list_active_accounts(agent, request.user)
            ],
            "selected_account_id": str(account.id),
        }
    )


@login_required
@require_http_methods(["POST"])
def agent_google_account(request, slug: str):
    agent = _get_agent_with_access(request.user, slug)
    account_id = str(request.POST.get("account_id") or "").strip()
    if not account_id:
        account = resolve_google_account(workspace=agent.workspace, owner=request.user)
        if account is None:
            return JsonResponse({"ok": False, "error": "Google account not found."}, status=404)
        active_accounts = _list_active_accounts(agent, request.user)
        return JsonResponse(
            {
                "ok": True,
                "account": _serialize_google_account(account, selected_id=str(account.id)),
                "selected_account_id": str(account.id),
                "accounts": [_serialize_google_account(item, selected_id=str(account.id)) for item in active_accounts],
            }
        )

    account = set_primary_google_account(workspace=agent.workspace, owner=request.user, account_id=account_id)
    if account is None:
        return JsonResponse({"ok": False, "error": "Google account not found."}, status=404)

    if request.headers.get("Accept", "").lower().find("application/json") >= 0:
        active_accounts = _list_active_accounts(agent, request.user)
        return JsonResponse(
            {
                "ok": True,
                "account": _serialize_google_account(account, selected_id=str(account.id)),
                "selected_account_id": str(account.id),
                "accounts": [_serialize_google_account(item, selected_id=str(account.id)) for item in active_accounts],
            }
        )

    messages.success(request, f"Selected {account.email or account.google_subject or account.id} as the default Google account.")
    return redirect(reverse("google_bridge:agent_connect", kwargs={"slug": slug}))


def _fallback_google_connect_url(request) -> str:
    state_map = _get_session_state_map(request)
    if state_map:
        last_state = next(reversed(state_map.values()))
        agent_slug = str(last_state.get("agent_slug") or "").strip()
        if agent_slug:
            return reverse("google_bridge:agent_connect", kwargs={"slug": agent_slug})
    return reverse("admin:index")
