from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from django.conf import settings


GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleOAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str]


def load_google_oauth_config() -> GoogleOAuthConfig:
    client_id = str(getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
    client_secret = str(getattr(settings, "GOOGLE_CLIENT_SECRET", "") or "").strip()
    redirect_uri = str(getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()
    scopes = list(getattr(settings, "GOOGLE_OAUTH_SCOPES", []) or [])
    if not client_id or not client_secret or not redirect_uri:
        raise GoogleOAuthError("Google OAuth is not configured.")
    return GoogleOAuthConfig(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scopes=scopes)


def build_authorization_url(*, state: str, access_type: str = "offline", prompt: str = "consent") -> str:
    config = load_google_oauth_config()
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "state": state,
        "access_type": access_type,
        "prompt": prompt,
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_authorization_code(code: str) -> dict[str, object]:
    config = load_google_oauth_config()
    payload = {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
    _raise_for_status(response)
    return dict(response.json())


def refresh_access_token(refresh_token: str) -> dict[str, object]:
    config = load_google_oauth_config()
    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
    _raise_for_status(response)
    return dict(response.json())


def fetch_userinfo(access_token: str) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        response = client.get("https://openidconnect.googleapis.com/v1/userinfo")
    _raise_for_status(response)
    return dict(response.json())


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:  # pragma: no cover - network failure path
        detail = exc.response.text.strip() if exc.response is not None else str(exc)
        raise GoogleOAuthError(detail or "Google OAuth request failed.") from exc
