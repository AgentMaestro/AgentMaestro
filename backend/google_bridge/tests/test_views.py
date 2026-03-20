from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from agents.models import Agent
from core.models import Workspace, WorkspaceMembership
from google_bridge.models import GoogleAccount

pytestmark = pytest.mark.django_db


@pytest.fixture
def google_agent():
    User = get_user_model()
    user = User.objects.create_user(username="googleviewer", password="x")
    workspace = Workspace.objects.create(name="Google Workspace")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, role=WorkspaceMembership.Role.OWNER)
    agent = Agent.objects.create(
        workspace=workspace,
        owner=user,
        created_by=user,
        name="Google Agent",
        soul="Connect Google",
        default_model="gpt-5",
    )
    return user, workspace, agent


def test_google_connect_redirects_to_authorization(monkeypatch, client, google_agent):
    user, _workspace, agent = google_agent
    client.force_login(user)

    monkeypatch.setattr(
        "google_bridge.services.oauth.build_authorization_url",
        lambda **kwargs: f"https://accounts.google.com/o/oauth2/v2/auth?state={kwargs['state']}",
    )

    response = client.post(reverse("google_bridge:agent_connect", kwargs={"slug": agent.slug}))

    assert response.status_code == 302
    assert "google.com/o/oauth2/v2/auth" in response["Location"]
    assert "google_bridge_oauth" in client.session


def test_google_callback_creates_account(monkeypatch, client, google_agent):
    user, _workspace, agent = google_agent
    client.force_login(user)
    session = client.session
    session["google_bridge_oauth"] = {"abc123": {"state": "abc123", "agent_slug": agent.slug, "workspace_id": str(agent.workspace_id)}}
    session.save()

    monkeypatch.setattr(
        "google_bridge.services.oauth.exchange_authorization_code",
        lambda code: {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "scope": "gmail.readonly calendar.readonly",
        },
    )
    monkeypatch.setattr(
        "google_bridge.services.oauth.fetch_userinfo",
        lambda access_token: {"sub": "subject-123", "email": "user@example.com"},
    )

    response = client.get(
        reverse("google_bridge:callback"),
        {"state": "abc123", "code": "auth-code"},
    )

    assert response.status_code == 302
    account = GoogleAccount.objects.get(workspace=agent.workspace, owner=user)
    assert account.google_subject == "subject-123"
    assert account.email == "user@example.com"
    assert account.is_active is True
    assert "google_bridge_oauth" not in client.session


def test_google_callback_rejects_unknown_state(client, google_agent):
    user, _workspace, agent = google_agent
    client.force_login(user)
    session = client.session
    session["google_bridge_oauth"] = {}
    session.save()

    response = client.get(reverse("google_bridge:callback"), {"state": "missing", "code": "auth-code"})

    assert response.status_code == 400


def test_google_status_reports_connection(client, google_agent):
    user, _workspace, agent = google_agent
    client.force_login(user)
    GoogleAccount.objects.create(workspace=agent.workspace, owner=user, google_subject="sub-1", email="user@example.com")

    response = client.get(reverse("google_bridge:agent_status", kwargs={"slug": agent.slug}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["email"] == "user@example.com"
    assert len(payload["accounts"]) == 1


def test_google_status_reports_multiple_active_accounts(client, google_agent):
    user, _workspace, agent = google_agent
    client.force_login(user)
    GoogleAccount.objects.create(workspace=agent.workspace, owner=user, google_subject="sub-1", email="first@example.com")
    GoogleAccount.objects.create(workspace=agent.workspace, owner=user, google_subject="sub-2", email="second@example.com")

    response = client.get(reverse("google_bridge:agent_status", kwargs={"slug": agent.slug}))

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert len(payload["accounts"]) == 2
