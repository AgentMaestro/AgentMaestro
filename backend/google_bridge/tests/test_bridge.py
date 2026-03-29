from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from core.models import Workspace
from google_bridge.models import GoogleAccount
from google_bridge.services.bridge import (
    GoogleBridgeTaskError,
    build_google_task_objective,
    execute_google_task,
    normalize_google_payload,
)
from google_bridge.services.schema import GOOGLE_BRIDGE_TOOL_EXAMPLES, build_google_bridge_args_schema


pytestmark = pytest.mark.django_db


def _make_account():
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge", password="x")
    workspace = Workspace.objects.create(name="Google Bridge Workspace")
    account = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-123",
        email="user@example.com",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/contacts",
        ],
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    account.set_tokens(access_token="access", refresh_token="refresh")
    account.save()
    return workspace, user, account


def test_normalize_google_payload_accepts_read_only_contract():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "query": "inbox newer_than:1d",
            "max_results": 5,
        }
    )

    assert payload["integration_kind"] == "google"
    assert payload["resource_kind"] == "gmail"
    assert payload["action_kind"] == "read"
    assert payload["operation"] == "list"
    assert payload["max_results"] == 5


def test_normalize_google_payload_accepts_step_plan():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "account_scope": "primary",
            "steps": [
                {
                    "resource_kind": "gmail",
                    "action_kind": "read",
                    "operation": "list",
                    "query": "inbox newer_than:1d",
                },
                {
                    "resource_kind": "calendar",
                    "action_kind": "read",
                    "operation": "list",
                    "calendar_id": "primary",
                },
            ],
        }
    )

    assert len(payload["steps"]) == 2
    assert payload["resource_kind"] == "gmail"
    assert payload["steps"][1]["resource_kind"] == "calendar"


def test_execute_google_task_requires_connection():
    with pytest.raises(GoogleBridgeTaskError, match="No active Google account connection is available"):
        execute_google_task(payload={"integration_kind": "google", "resource_kind": "gmail", "action_kind": "read"})


def test_execute_google_task_returns_json_summary(monkeypatch):
    workspace, user, account = _make_account()

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 10):
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "List snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "List subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "query": "inbox newer_than:1d",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert result["ok"] is True
    assert result["integration_kind"] == "google"
    assert result["resource_kind"] == "gmail"
    assert result["action_kind"] == "read"
    assert result["operation"] == "list"
    assert result["result"]["messages"][0]["id"] == "msg-1"
    assert result["result"]["messages"][0]["subject"] == "List subject"
    assert result["result"]["messages"][0]["from"] == "sender@example.com"
    assert result["result"]["messages"][0]["date"] == "Fri, 21 Mar 2026 09:00:00 -0400"
    assert "Returned 1 Gmail messages" in result["summary_text"]
    assert "use google_bridge with operation=read" in result["summary_text"].lower()


def test_execute_google_task_defaults_gmail_list_to_unread(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 10):
            captured["query"] = query
            captured["label_ids"] = label_ids
            captured["max_results"] = max_results
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Unread snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Unread subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["query"] == "is:unread"
    assert captured["label_ids"] == []
    assert captured["max_results"] == 5
    assert "Returned 1 unread Gmail messages" in result["summary_text"]
    assert result["result"]["messages"][0]["subject"] == "Unread subject"


def test_execute_google_task_can_include_read_gmail_messages(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 10):
            captured["query"] = query
            captured["label_ids"] = label_ids
            captured["max_results"] = max_results
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Read snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Read subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["query"] == ""
    assert captured["label_ids"] == []
    assert captured["max_results"] == 5
    assert "Returned 1 Gmail messages" in result["summary_text"]
    assert "unread" not in result["summary_text"].lower()
    assert result["result"]["messages"][0]["subject"] == "Read subject"


def test_execute_google_task_splits_or_queries_for_gmail_list(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            if query == "from:info@airbnb.com":
                return {"messages": [{"id": f"airbnb-{index}"} for index in range(1, 11)], "resultSizeEstimate": 10}
            if query == "from:airbnb.com":
                return {"messages": [{"id": f"domain-{index}"} for index in range(1, 11)], "resultSizeEstimate": 10}
            if query == "subject:Airbnb":
                return {"messages": [{"id": f"subject-{index}"} for index in range(1, 11)], "resultSizeEstimate": 10}
            return {"messages": [], "resultSizeEstimate": 0}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": 'from:info@airbnb.com OR from:airbnb.com OR subject:("Airbnb")',
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert [call["query"] for call in captured["list_calls"]] == [
        "from:info@airbnb.com",
        "from:airbnb.com",
        "subject:Airbnb",
    ]
    assert all(call["max_results"] == 20 for call in captured["list_calls"])
    assert result["result"]["resultSizeEstimate"] == 20
    assert len(result["result"]["messages"]) == 20
    assert "Returned 20 Gmail messages" in result["summary_text"]
    assert result["result"]["query_plan"]["call_count"] == 3


def test_execute_google_task_uses_shared_corpus_for_large_gmail_or_list(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": []}
    matching_domains = [
        "kayak.com",
        "hulumail.com",
        "ally.com",
        "fast-growing-trees.com",
        "bulksupplements.com",
        "seekingalpha.com",
        "email.interactivebrokers.com",
        "alibaba.com",
        "instagram.com",
        "flyfrontier.com",
    ]

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            if query != "in:anywhere":
                return {"messages": [], "resultSizeEstimate": 0}
            messages = [{"id": f"match-{index}"} for index in range(1, 11)]
            messages.extend({"id": f"noise-{index}"} for index in range(1, 6))
            return {"messages": messages, "resultSizeEstimate": len(messages)}

        def get_gmail_message(self, message_id: str):
            if message_id.startswith("match-"):
                index = int(message_id.split("-", 1)[1]) - 1
                domain = matching_domains[index]
                sender = f"Sender <alerts@{domain}>"
            else:
                sender = "Noise <noise@example.com>"
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": sender},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "max_results": 50,
            "query": "in:anywhere from:(kayak.com OR hulumail.com OR ally.com OR fast-growing-trees.com OR bulksupplements.com OR seekingalpha.com OR email.interactivebrokers.com OR alibaba.com OR instagram.com OR flyfrontier.com)",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert [call["query"] for call in captured["list_calls"]] == ["in:anywhere"]
    assert result["result"]["execution_strategy"] == "shared_corpus"
    assert result["result"]["resultSizeEstimate"] == 10
    assert len(result["result"]["messages"]) == 10
    assert "Returned 10 Gmail messages" in result["summary_text"]


def test_execute_google_task_uses_shared_corpus_for_large_sender_or_list(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": []}
    matching_domains = [
        "kayak.com",
        "hulumail.com",
        "ally.com",
        "fast-growing-trees.com",
        "bulksupplements.com",
        "seekingalpha.com",
        "email.interactivebrokers.com",
        "alibaba.com",
        "instagram.com",
        "flyfrontier.com",
    ]

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            if query not in {"", "in:anywhere"}:
                return {"messages": [], "resultSizeEstimate": 0}
            messages = [{"id": f"match-{index}"} for index in range(1, 11)]
            messages.extend({"id": f"noise-{index}"} for index in range(1, 6))
            return {"messages": messages, "resultSizeEstimate": len(messages)}

        def get_gmail_message(self, message_id: str):
            if message_id.startswith("match-"):
                index = int(message_id.split("-", 1)[1]) - 1
                domain = matching_domains[index]
                sender = f"Sender <alerts@{domain}>"
            else:
                sender = "Noise <noise@example.com>"
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": sender},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "max_results": 50,
            "query": "from:(kayak.com OR hulumail.com OR ally.com OR fast-growing-trees.com OR bulksupplements.com OR seekingalpha.com OR email.interactivebrokers.com OR alibaba.com OR instagram.com OR flyfrontier.com)",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert [call["query"] for call in captured["list_calls"]] == [""]
    assert result["result"]["execution_strategy"] == "shared_corpus"
    assert result["result"]["resultSizeEstimate"] == 10
    assert len(result["result"]["messages"]) == 10
    assert "Returned 10 Gmail messages" in result["summary_text"]


def test_execute_google_task_supports_nested_or_inside_parentheses():
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(query)
            if query == "subject:failure":
                return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}
            if query == "subject:error":
                return {"messages": [{"id": "msg-2"}], "resultSizeEstimate": 1}
            return {"messages": [], "resultSizeEstimate": 0}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": "subject:(failure OR error)",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["list_calls"] == ["subject:failure", "subject:error"]
    assert result["result"]["query_plan"]["call_count"] == 2
    assert result["result"]["resultSizeEstimate"] == 2


def test_execute_google_task_supports_grouped_or_and_not_for_gmail_list(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(query)
            if query in {"from:dsmith@aol.com -label_ids:promotions", "from:dsmyth@aol.com -label_ids:promotions"}:
                return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}
            return {"messages": [], "resultSizeEstimate": 0}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": "from:(dsmith@aol.com OR dsmyth@aol.com) AND NOT label_ids:promotions",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["list_calls"] == [
        "from:dsmith@aol.com -label_ids:promotions",
        "from:dsmyth@aol.com -label_ids:promotions",
    ]
    assert result["result"]["query_plan"]["call_count"] == 2
    assert result["result"]["resultSizeEstimate"] == 1


@override_settings(GMAIL_OR_CLAUSE_LIMIT=2)
def test_execute_google_task_rejects_or_queries_over_clause_cap():
    workspace, user, account = _make_account()

    with pytest.raises(GoogleBridgeTaskError, match="expanded to 3 clauses, which exceeds the limit of 2"):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "gmail",
                "action_kind": "read",
                "operation": "list",
                "account_scope": "primary",
                "email": "user@example.com",
                "include_read": True,
                "query": "from:a@example.com OR from:b@example.com OR from:c@example.com",
            },
            workspace=workspace,
            owner=user,
            account=account,
        )


def test_execute_google_task_can_read_gmail_with_query_filters(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 20, page_token: str = ""):
            captured["query"] = query
            captured["label_ids"] = label_ids
            captured["max_results"] = max_results
            captured["page_token"] = page_token
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Query snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Query subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "read",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": "from:airbnb.com",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["query"] == "from:airbnb.com"
    assert captured["label_ids"] == []
    assert captured["max_results"] == 20
    assert captured["page_token"] == ""
    assert "Returned 1 Gmail messages" in result["summary_text"]
    assert result["result"]["messages"][0]["subject"] == "Query subject"


def test_execute_google_task_normalizes_sender_domain_query(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 20, page_token: str = ""):
            captured["query"] = query
            captured["label_ids"] = label_ids
            captured["max_results"] = max_results
            captured["page_token"] = page_token
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Normalized snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Normalized subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "read",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": "from:@airbnb.com",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["query"] == "from:airbnb.com"
    assert captured["label_ids"] == []
    assert captured["max_results"] == 20
    assert captured["page_token"] == ""
    assert "Returned 1 Gmail messages" in result["summary_text"]
    assert result["result"]["messages"][0]["subject"] == "Normalized subject"


def test_execute_google_task_normalizes_spaced_sender_domain_query(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 20, page_token: str = ""):
            captured["query"] = query
            captured["label_ids"] = label_ids
            captured["max_results"] = max_results
            captured["page_token"] = page_token
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Spaced snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Spaced subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "read",
            "account_scope": "primary",
            "email": "user@example.com",
            "include_read": True,
            "query": "from: @airbnb.com",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["query"] == "from:airbnb.com"
    assert captured["label_ids"] == []
    assert captured["max_results"] == 20
    assert captured["page_token"] == ""
    assert "Returned 1 Gmail messages" in result["summary_text"]
    assert result["result"]["messages"][0]["subject"] == "Spaced subject"


def test_execute_google_task_creates_gmail_draft(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def create_gmail_draft(self, *, to: list[str], subject: str = "", body: str = "", cc: list[str] | None = None, bcc: list[str] | None = None, thread_id: str = ""):
            captured["to"] = to
            captured["subject"] = subject
            captured["body"] = body
            captured["cc"] = cc
            captured["bcc"] = bcc
            captured["thread_id"] = thread_id
            return {"draft": {"id": "draft-1", "message": {"id": "msg-1", "threadId": "thread-1"}}}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "draft",
            "operation": "create",
            "account_scope": "primary",
            "email": "user@example.com",
            "to": ["friend@example.com"],
            "subject": "Draft subject",
            "body": "Draft body",
            "thread_id": "thread-1",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["to"] == ["friend@example.com"]
    assert captured["subject"] == "Draft subject"
    assert result["action_kind"] == "draft"
    assert result["operation"] == "create"
    assert "Gmail draft created" in result["summary_text"]
    assert "Draft ID: draft-1" in result["summary_text"]


def test_execute_google_task_sends_gmail_message(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def send_gmail_message(self, *, to: list[str], subject: str = "", body: str = "", cc: list[str] | None = None, bcc: list[str] | None = None, thread_id: str = ""):
            captured["to"] = to
            captured["subject"] = subject
            captured["body"] = body
            captured["cc"] = cc
            captured["bcc"] = bcc
            captured["thread_id"] = thread_id
            return {"id": "msg-123", "threadId": "thread-123"}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "send",
            "operation": "send",
            "account_scope": "primary",
            "email": "user@example.com",
            "to": ["friend@example.com"],
            "subject": "Send subject",
            "body": "Send body",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["to"] == ["friend@example.com"]
    assert captured["subject"] == "Send subject"
    assert result["action_kind"] == "send"
    assert result["operation"] == "send"
    assert "Gmail message sent" in result["summary_text"]
    assert "Message ID: msg-123" in result["summary_text"]


def test_execute_google_task_lists_gmail_filters(monkeypatch):
    workspace, user, account = _make_account()

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_filters(self):
            return {
                "filter": [
                    {
                        "id": "filter-1",
                        "criteria": {"from": "alerts@example.com", "query": "from:alerts@example.com"},
                        "action": {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]},
                    }
                ]
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert result["resource_kind"] == "gmail_settings"
    assert result["operation"] == "list"
    assert result["result"]["filter"][0]["id"] == "filter-1"
    assert "Returned 1 Gmail filters" in result["summary_text"]


def test_execute_google_task_creates_updates_and_deletes_gmail_filters(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"calls": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def get_gmail_filter(self, filter_id: str):
            captured["calls"].append(("get", filter_id))
            return {
                "id": filter_id,
                "criteria": {"from": "alerts@example.com"},
                "action": {"addLabelIds": ["Label_1"]},
            }

        def create_gmail_filter(self, *, criteria: dict | None = None, action: dict | None = None):
            captured["calls"].append(("create", criteria, action))
            return {"id": "filter-2", "criteria": criteria or {}, "action": action or {}}

        def delete_gmail_filter(self, filter_id: str):
            captured["calls"].append(("delete", filter_id))
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    create_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "email": "user@example.com",
            "criteria": {"from": "alerts@example.com", "query": "from:alerts@example.com"},
            "action": {"addLabelIds": ["Label_1"]},
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    update_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "update",
            "operation": "update",
            "account_scope": "primary",
            "email": "user@example.com",
            "filter_id": "filter-1",
            "action": {"removeLabelIds": ["INBOX"]},
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    delete_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "delete",
            "operation": "delete",
            "account_scope": "primary",
            "email": "user@example.com",
            "filter_id": "filter-1",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert create_result["result"]["id"] == "filter-2"
    assert update_result["result"]["id"] == "filter-2"
    assert delete_result["operation"] == "delete"
    assert captured["calls"][0][0] == "create"
    assert captured["calls"][1] == ("get", "filter-1")
    assert captured["calls"][2][0] == "delete"
    assert captured["calls"][3][0] == "create"
    assert captured["calls"][4] == ("delete", "filter-1")
    assert "Gmail filter created" in create_result["summary_text"]
    assert "Gmail filter updated" in update_result["summary_text"]
    assert "Gmail filter deleted" in delete_result["summary_text"]


def test_execute_google_task_fans_out_gmail_filter_query_clauses(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"created": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def create_gmail_filter(self, *, criteria: dict | None = None, action: dict | None = None):
            captured["created"].append((dict(criteria or {}), dict(action or {})))
            return {"id": f"filter-{len(captured['created'])}", "criteria": criteria or {}, "action": action or {}}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "email": "user@example.com",
            "criteria": {
                "query": "from:alerts@example.com OR from:news@example.com",
                "subject": "Alert",
            },
            "action": {"addLabelIds": ["Label_1"]},
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert len(captured["created"]) == 2
    assert captured["created"][0][0]["query"] == "from:alerts@example.com"
    assert captured["created"][1][0]["query"] == "from:news@example.com"
    assert result["result"]["count"] == 2
    assert "Gmail filters created" in result["summary_text"]


def test_execute_google_task_previews_gmail_filter_creation(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"queries": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 5, page_token: str = ""):
            captured["queries"].append((query, max_results))
            return {
                "messages": [{"id": f"msg-{query or 'none'}"}],
                "resultSizeEstimate": 1,
            }

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "email": "user@example.com",
            "dry_run": True,
            "preview_max_results": 3,
            "criteria": {
                "query": "from:alerts@example.com OR from:news@example.com",
            },
            "action": {"addLabelIds": ["Label_1"]},
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert len(captured["queries"]) == 2
    assert captured["queries"][0] == ("from:alerts@example.com", 3)
    assert captured["queries"][1] == ("from:news@example.com", 3)
    assert result["result"]["preview_max_results"] == 3
    assert len(result["result"]["preview_filters"]) == 2
    assert "preview ready" in result["summary_text"].lower()


def test_execute_google_task_previews_gmail_filter_creation_with_shared_corpus(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"queries": []}
    matching_domains = [
        "kayak.com",
        "hulumail.com",
        "ally.com",
        "fast-growing-trees.com",
        "bulksupplements.com",
        "seekingalpha.com",
        "email.interactivebrokers.com",
        "alibaba.com",
        "instagram.com",
        "flyfrontier.com",
    ]

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 5, page_token: str = ""):
            captured["queries"].append((query, max_results))
            if query != "in:anywhere":
                return {"messages": [], "resultSizeEstimate": 0}
            messages = [{"id": f"match-{index}"} for index in range(1, 11)]
            messages.extend({"id": f"noise-{index}"} for index in range(1, 6))
            return {"messages": messages, "resultSizeEstimate": len(messages)}

        def get_gmail_message(self, message_id: str):
            if message_id.startswith("match-"):
                index = int(message_id.split("-", 1)[1]) - 1
                sender = f"Sender <alerts@{matching_domains[index]}>"
            else:
                sender = "Noise <noise@example.com>"
            return {
                "snippet": f"Snippet for {message_id}",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": sender},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail_settings",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "email": "user@example.com",
            "dry_run": True,
            "preview_max_results": 3,
            "criteria": {
                "query": "in:anywhere from:(kayak.com OR hulumail.com OR ally.com OR fast-growing-trees.com OR bulksupplements.com OR seekingalpha.com OR email.interactivebrokers.com OR alibaba.com OR instagram.com OR flyfrontier.com)",
            },
            "action": {"addLabelIds": ["Label_1"]},
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert [call[0] for call in captured["queries"]] == ["in:anywhere"]
    assert result["result"]["shared_base_query"] == "in:anywhere"
    assert len(result["result"]["preview_filters"]) == 10
    assert all(item["resultSizeEstimate"] == 1 for item in result["result"]["preview_filters"])
    assert "preview ready" in result["summary_text"].lower()


def test_execute_google_task_trashes_gmail_message(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def trash_gmail_message(self, message_id: str):
            captured["message_id"] = message_id
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "trash",
            "account_scope": "primary",
            "email": "user@example.com",
            "message_id": "msg-123",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["message_id"] == "msg-123"
    assert result["action_kind"] == "delete"
    assert result["operation"] == "trash"
    assert "moved to trash" in result["summary_text"]


def test_execute_google_task_trash_can_bulk_resolve_query(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": [], "trashed_ids": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 10,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                    "email": self.connection.email,
                }
            )
            return {"messages": [{"id": "msg-789"}, {"id": "msg-790"}], "resultSizeEstimate": 2}

        def trash_gmail_message(self, message_id: str):
            captured["trashed_ids"].append(message_id)
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "account_scope": "primary",
            "email": "user@example.com",
            "query": "from:airbnb.com",
            "max_results": 100,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["list_calls"][0]["query"] == "from:airbnb.com"
    assert captured["list_calls"][0]["max_results"] == 100
    assert captured["list_calls"][0]["page_token"] == ""
    assert captured["trashed_ids"] == ["msg-789", "msg-790"]
    assert result["action_kind"] == "delete"
    assert result["operation"] == "trash"
    assert "Count: 2" in result["summary_text"]
    assert "moved to trash" in result["summary_text"]


def test_execute_google_task_permanently_deletes_gmail_message(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def delete_gmail_message(self, message_id: str):
            captured["message_id"] = message_id
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "delete",
            "account_scope": "primary",
            "email": "user@example.com",
            "message_id": "msg-456",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["message_id"] == "msg-456"
    assert result["action_kind"] == "delete"
    assert result["operation"] == "delete"
    assert "permanently deleted" in result["summary_text"]


def test_normalize_google_payload_defaults_gmail_delete_to_trash():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "message_id": "msg-123",
        }
    )

    assert payload["action_kind"] == "delete"
    assert payload["operation"] == "trash"


def test_normalize_google_payload_respects_gmail_delete_mode():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "delete",
            "delete_mode": "trash",
            "message_id": "msg-123",
        }
    )

    assert payload["action_kind"] == "delete"
    assert payload["operation"] == "trash"


def test_execute_google_task_can_merge_multiple_accounts(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-merge", password="x")
    workspace = Workspace.objects.create(name="Google Merge Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 10):
            return {"messages": [{"id": f"{self.connection.email}-msg"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Account snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": f"Subject for {message_id}"},
                        {"name": "From", "value": f"sender@{self.connection.email}"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "all",
            "query": "inbox newer_than:1d",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
    )

    assert len(result["accounts"]) == 2
    assert result["result"]["resultSizeEstimate"] == 2
    assert len(result["result"]["messages"]) == 2
    assert result["result"]["messages"][0]["subject"].startswith("Subject for ")
    assert "Returned 2 Gmail messages across 2 accounts" in result["summary_text"]


def test_execute_google_task_can_bulk_trash_query_across_all_accounts(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-bulk-trash", password="x")
    workspace = Workspace.objects.create(name="Google Bulk Trash Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    captured: dict[str, list[dict[str, object]]] = {"list_calls": [], "trashed": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 10,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "email": self.connection.email,
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            return {"messages": [{"id": f"{self.connection.email}-airbnb"}], "resultSizeEstimate": 1}

        def trash_gmail_message(self, message_id: str):
            captured["trashed"].append({"email": self.connection.email, "message_id": message_id})
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "trash",
            "account_scope": "all",
            "query": "from:airbnb.com",
            "include_read": True,
            "max_results": 100,
        },
        workspace=workspace,
        owner=user,
    )

    assert len(result["accounts"]) == 2
    assert result["result"]["deleted_message_ids"] == ["first@example.com-airbnb", "second@example.com-airbnb"]
    assert len(captured["list_calls"]) == 2
    assert len(captured["trashed"]) == 2
    assert {item["email"] for item in captured["trashed"]} == {"first@example.com", "second@example.com"}
    assert "across 2 accounts" in result["summary_text"]


def test_execute_google_task_can_skip_empty_accounts_during_bulk_trash(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-bulk-trash-skip", password="x")
    workspace = Workspace.objects.create(name="Google Bulk Trash Skip Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-skip-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-skip-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    captured: dict[str, list[dict[str, object]]] = {"list_calls": [], "trashed": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "email": self.connection.email,
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            if self.connection.email == "first@example.com":
                return {"messages": [], "resultSizeEstimate": 0}
            return {"messages": [{"id": "second@example.com-airbnb"}], "resultSizeEstimate": 1}

        def trash_gmail_message(self, message_id: str):
            captured["trashed"].append({"email": self.connection.email, "message_id": message_id})
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "trash",
            "account_scope": "all",
            "query": "from:airbnb.com",
            "include_read": True,
            "max_results": 200,
        },
        workspace=workspace,
        owner=user,
    )

    assert len(result["accounts"]) == 2
    assert result["result"]["deleted_message_ids"] == ["second@example.com-airbnb"]
    assert len(captured["list_calls"]) == 2
    assert len(captured["trashed"]) == 1
    assert captured["trashed"][0]["email"] == "second@example.com"
    assert "across 2 accounts" in result["summary_text"]


def test_execute_google_task_returns_completed_noop_when_bulk_trash_matches_nothing(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-bulk-trash-none", password="x")
    workspace = Workspace.objects.create(name="Google Bulk Trash None Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-none-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-trash-none-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    captured: dict[str, list[dict[str, object]]] = {"list_calls": [], "trashed": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 20,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "email": self.connection.email,
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            return {"messages": [], "resultSizeEstimate": 0}

        def trash_gmail_message(self, message_id: str):
            captured["trashed"].append({"email": self.connection.email, "message_id": message_id})
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "trash",
            "account_scope": "all",
            "query": "from:seekingalpha.com",
            "include_read": True,
            "max_results": 200,
        },
        workspace=workspace,
        owner=user,
    )

    assert result["ok"] is True
    assert len(result["accounts"]) == 2
    assert result["result"]["deleted_message_ids"] == []
    assert result["result"]["matched_queries"] == ["from:seekingalpha.com"]
    assert len(captured["list_calls"]) == 2
    assert len(captured["trashed"]) == 0
    assert "No Gmail messages matched this cleanup query" in result["summary_text"]
    assert "first@example.com" in result["summary_text"]
    assert "second@example.com" in result["summary_text"]


def test_execute_google_task_splits_or_queries_for_bulk_trash(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"list_calls": [], "trashed_ids": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(
            self,
            *,
            query: str = "",
            label_ids: list[str] | None = None,
            max_results: int = 10,
            page_token: str = "",
        ):
            captured["list_calls"].append(
                {
                    "query": query,
                    "label_ids": label_ids,
                    "max_results": max_results,
                    "page_token": page_token,
                }
            )
            if "airbnb" in query:
                return {"messages": [{"id": "msg-airbnb"}], "resultSizeEstimate": 1}
            if "travelzoo" in query:
                return {"messages": [{"id": "msg-travelzoo"}], "resultSizeEstimate": 1}
            if "petco" in query:
                return {"messages": [{"id": "msg-petco"}], "resultSizeEstimate": 1}
            return {"messages": [], "resultSizeEstimate": 0}

        def trash_gmail_message(self, message_id: str):
            captured["trashed_ids"].append(message_id)
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "gmail",
            "action_kind": "delete",
            "operation": "trash",
            "account_scope": "primary",
            "query": "from:airbnb.com OR from:us.travelzoo.com OR from:e.petco.com",
            "max_results": 100,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert [call["query"] for call in captured["list_calls"]] == [
        "from:airbnb.com",
        "from:us.travelzoo.com",
        "from:e.petco.com",
    ]
    assert captured["trashed_ids"] == ["msg-airbnb", "msg-travelzoo", "msg-petco"]
    assert result["result"]["deleted_message_ids"] == ["msg-airbnb", "msg-travelzoo", "msg-petco"]
    assert "Count: 3" in result["summary_text"]


def test_execute_google_task_runs_multi_step_read_plan(monkeypatch):
    workspace, user, account = _make_account()

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_gmail_messages(self, *, query: str = "", label_ids: list[str] | None = None, max_results: int = 10):
            return {"messages": [{"id": "msg-1"}], "resultSizeEstimate": 1}

        def get_gmail_message(self, message_id: str):
            return {
                "snippet": "Test snippet",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Test subject"},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Date", "value": "Fri, 21 Mar 2026 09:00:00 -0400"},
                    ]
                },
            }

        def list_calendar_events(self, *, calendar_id: str = "primary", q: str = "", time_min: str = "", time_max: str = "", max_results: int = 10):
            self.received_time_min = time_min
            self.received_time_max = time_max
            return {"items": [{"id": "event-1", "summary": "Standup"}]}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "account_scope": "primary",
            "steps": [
                {
                    "resource_kind": "gmail",
                    "action_kind": "read",
                    "operation": "list",
                    "query": "inbox newer_than:1d",
                },
                {
                    "resource_kind": "calendar",
                    "action_kind": "read",
                    "operation": "list",
                    "calendar_id": "primary",
                },
            ],
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert len(result["steps"]) == 2
    assert result["steps"][0]["resource_kind"] == "gmail"
    assert result["steps"][1]["resource_kind"] == "calendar"
    assert "1. Returned 1 Gmail messages" in result["summary_text"]
    assert "2. Found 1 calendar events" in result["summary_text"]
    assert account.access_token  # ensure the account fixture remains valid


def test_execute_google_task_normalizes_calendar_bounds_to_eastern_time(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, str] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_calendar_events(self, *, calendar_id: str = "primary", q: str = "", time_min: str = "", time_max: str = "", max_results: int = 10):
            captured["time_min"] = time_min
            captured["time_max"] = time_max
            return {"items": [{"id": "event-1", "summary": "Standup"}]}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "read",
            "operation": "list",
            "calendar_id": "primary",
            "time_min": "2026-03-20T00:00:00",
            "time_max": "2026-03-21T00:00:00Z",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["time_min"] == "2026-03-20T00:00:00-04:00"
    assert captured["time_max"] == "2026-03-20T20:00:00-04:00"
    assert result["steps"][0]["resource_kind"] == "calendar"


def test_execute_google_task_lists_all_connected_account_calendars_when_calendar_id_is_omitted(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, list[str]] = {"calendar_ids": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_calendar_list(self):
            return {
                "items": [
                    {"id": "primary", "summary": "Primary Calendar", "primary": True},
                    {"id": "shared", "summary": "Shared Calendar", "selected": True},
                ]
            }

        def list_calendar_events(self, *, calendar_id: str = "primary", q: str = "", time_min: str = "", time_max: str = "", max_results: int = 10):
            captured["calendar_ids"].append(calendar_id)
            if calendar_id == "shared":
                return {
                    "items": [
                        {
                            "id": "event-1",
                            "summary": "Shared calendar event",
                            "start": {"dateTime": "2026-03-22T14:00:00-04:00"},
                        }
                    ]
                }
            return {"items": []}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "time_min": "2026-03-22T00:00:00",
            "time_max": "2026-03-23T00:00:00",
            "max_results": 50,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["calendar_ids"] == ["primary", "shared"]
    assert len(result["result"]["items"]) == 1
    assert result["result"]["items"][0]["calendar_id"] == "shared"
    assert result["result"]["items"][0]["calendar_summary"] == "Shared Calendar"
    assert len(result["result"]["calendars"]) == 2
    assert "Found 1 calendar events" in result["summary_text"]


def test_execute_google_task_supports_grouped_or_for_calendar_list(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, list[tuple[str, str]]] = {"list_calls": []}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_calendar_events(self, *, calendar_id: str = "primary", q: str = "", time_min: str = "", time_max: str = "", max_results: int = 10):
            captured["list_calls"].append((calendar_id, q))
            if q in {"team sync", "planning"}:
                return {
                    "items": [
                        {
                            "id": "event-1",
                            "summary": "Team sync",
                            "start": {"dateTime": "2026-03-22T09:00:00-04:00"},
                        }
                    ]
                }
            return {"items": []}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "read",
            "operation": "list",
            "account_scope": "primary",
            "email": "user@example.com",
            "calendar_id": "primary",
            "query": "q:(team sync OR planning)",
            "time_min": "2026-03-22T00:00:00",
            "time_max": "2026-03-23T00:00:00",
            "max_results": 10,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["list_calls"] == [("primary", "team sync"), ("primary", "planning")]
    assert result["result"]["query_plan"]["call_count"] == 2
    assert result["result"]["resultSizeEstimate"] == 1
    assert len(result["result"]["items"]) == 1
    assert result["result"]["items"][0]["calendar_id"] == "primary"
    assert "Found 1 calendar events" in result["summary_text"]


def test_execute_google_task_creates_calendar_event(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def create_calendar_event(
            self,
            *,
            calendar_id: str = "primary",
            summary: str = "",
            description: str = "",
            location: str = "",
            start: dict | None = None,
            end: dict | None = None,
            attendees: list[str] | None = None,
            send_updates: str = "",
        ):
            captured["calendar_id"] = calendar_id
            captured["summary"] = summary
            captured["description"] = description
            captured["location"] = location
            captured["start"] = start
            captured["end"] = end
            captured["attendees"] = attendees
            captured["send_updates"] = send_updates
            return {"id": "event-1", "htmlLink": "https://calendar.google.com/event?eid=event-1", "summary": summary}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "calendar_id": "primary",
            "summary": "Calendar write test",
            "description": "Smoke test event",
            "location": "Court 1",
            "start": "2026-03-22T09:00:00",
            "end": "2026-03-22T09:30:00",
            "time_zone": "America/New_York",
            "attendees": ["someone@example.com"],
            "send_updates": "none",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["calendar_id"] == "primary"
    assert captured["summary"] == "Calendar write test"
    assert captured["start"] == {"dateTime": "2026-03-22T09:00:00-04:00", "timeZone": "America/New_York"}
    assert captured["end"] == {"dateTime": "2026-03-22T09:30:00-04:00", "timeZone": "America/New_York"}
    assert captured["attendees"] == ["someone@example.com"]
    assert captured["send_updates"] == "none"
    assert result["action_kind"] == "create"
    assert result["operation"] == "create"
    assert "Calendar event created" in result["summary_text"]
    assert "Event ID: event-1" in result["summary_text"]


@override_settings(TIME_ZONE="Europe/London")
def test_execute_google_task_defaults_calendar_timezone_to_local_setting(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def create_calendar_event(
            self,
            *,
            calendar_id: str = "primary",
            summary: str = "",
            description: str = "",
            location: str = "",
            start: dict | None = None,
            end: dict | None = None,
            attendees: list[str] | None = None,
            send_updates: str = "",
        ):
            captured["calendar_id"] = calendar_id
            captured["start"] = start
            captured["end"] = end
            return {"id": "event-1", "htmlLink": "https://calendar.google.com/event?eid=event-1", "summary": summary}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "calendar_id": "primary",
            "summary": "Calendar write test",
            "description": "Smoke test event",
            "location": "Court 1",
            "start": "2026-03-22T09:00:00",
            "end": "2026-03-22T09:30:00",
            "attendees": ["someone@example.com"],
            "send_updates": "none",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["calendar_id"] == "primary"
    assert captured["start"] == {"dateTime": "2026-03-22T09:00:00+00:00", "timeZone": "Europe/London"}
    assert captured["end"] == {"dateTime": "2026-03-22T09:30:00+00:00", "timeZone": "Europe/London"}


def test_execute_google_task_updates_calendar_event(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def update_calendar_event(
            self,
            *,
            calendar_id: str = "primary",
            event_id: str,
            summary: str = "",
            description: str = "",
            location: str = "",
            start: dict | None = None,
            end: dict | None = None,
            attendees: list[str] | None = None,
            send_updates: str = "",
        ):
            captured["calendar_id"] = calendar_id
            captured["event_id"] = event_id
            captured["summary"] = summary
            captured["description"] = description
            captured["location"] = location
            captured["start"] = start
            captured["end"] = end
            captured["attendees"] = attendees
            captured["send_updates"] = send_updates
            return {"id": event_id, "htmlLink": "https://calendar.google.com/event?eid=event-2", "summary": summary}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "update",
            "operation": "update",
            "account_scope": "primary",
            "calendar_id": "primary",
            "event_id": "event-2",
            "summary": "Calendar write test updated",
            "start": "2026-03-22T10:00:00",
            "end": "2026-03-22T10:30:00",
            "time_zone": "America/New_York",
            "attendees": ["someone@example.com"],
            "send_updates": "all",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["event_id"] == "event-2"
    assert captured["summary"] == "Calendar write test updated"
    assert captured["start"] == {"dateTime": "2026-03-22T10:00:00-04:00", "timeZone": "America/New_York"}
    assert captured["end"] == {"dateTime": "2026-03-22T10:30:00-04:00", "timeZone": "America/New_York"}
    assert captured["attendees"] == ["someone@example.com"]
    assert captured["send_updates"] == "all"
    assert result["action_kind"] == "update"
    assert result["operation"] == "update"
    assert "Calendar event updated" in result["summary_text"]
    assert "Event ID: event-2" in result["summary_text"]


def test_execute_google_task_deletes_calendar_event(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def delete_calendar_event(self, *, calendar_id: str = "primary", event_id: str, send_updates: str = ""):
            captured["calendar_id"] = calendar_id
            captured["event_id"] = event_id
            captured["send_updates"] = send_updates
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "delete",
            "operation": "delete",
            "account_scope": "primary",
            "calendar_id": "primary",
            "event_id": "event-3",
            "send_updates": "none",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["event_id"] == "event-3"
    assert captured["send_updates"] == "none"
    assert result["action_kind"] == "delete"
    assert result["operation"] == "delete"
    assert "Calendar event deleted" in result["summary_text"]
    assert "Event ID: event-3" in result["summary_text"]


def test_execute_google_task_requires_specific_account_for_read_by_id():
    workspace, user, _account = _make_account()

    with pytest.raises(GoogleBridgeTaskError, match="Use the returned account_email or google_subject"):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "gmail",
                "action_kind": "read",
                "operation": "read",
                "account_scope": "all",
                "message_id": "msg-123",
            },
            workspace=workspace,
            owner=user,
        )


def test_execute_google_task_requires_specific_account_for_gmail_write():
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-write", password="x")
    workspace = Workspace.objects.create(name="Google Write Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-write-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-write-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    with pytest.raises(GoogleBridgeTaskError, match="require a specific connected account"):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "gmail",
                "action_kind": "draft",
                "operation": "create",
                "account_scope": "primary",
                "to": ["friend@example.com"],
                "subject": "Draft subject",
                "body": "Draft body",
            },
            workspace=workspace,
            owner=user,
        )


def test_execute_google_task_requires_specific_account_for_calendar_write():
    User = get_user_model()
    user = User.objects.create_user(username="googlebridge-calendar-write", password="x")
    workspace = Workspace.objects.create(name="Google Calendar Write Workspace")
    first = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-cal-1",
        email="first@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    first.set_tokens(access_token="access-1", refresh_token="refresh-1")
    first.save()
    second = GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-cal-2",
        email="second@example.com",
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )
    second.set_tokens(access_token="access-2", refresh_token="refresh-2")
    second.save()

    with pytest.raises(GoogleBridgeTaskError, match="require a specific connected account"):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "calendar",
                "action_kind": "create",
                "operation": "create",
                "account_scope": "primary",
                "summary": "Calendar write test",
                "start": "2026-03-22T09:00:00",
                "end": "2026-03-22T09:30:00",
            },
            workspace=workspace,
            owner=user,
        )


def test_normalize_google_payload_rejects_non_read_step():
    with pytest.raises(GoogleBridgeTaskError, match="supports read, draft, send, and delete actions"):
        normalize_google_payload(
            {
                "integration_kind": "google",
                "steps": [
                    {
                        "resource_kind": "gmail",
                        "action_kind": "archive",
                        "operation": "remove",
                    }
                ],
            }
        )


def test_build_google_task_objective_builds_compact_instruction_prompt():
    objective, prompt = build_google_task_objective(
        {
            "integration_kind": "google",
            "resource_kind": "calendar",
            "action_kind": "read",
            "operation": "list",
            "calendar_id": "primary",
            "time_min": "2026-03-20T00:00:00-04:00",
        }
    )

    assert "Google bridge task" in objective
    assert "Use the Google Bridge tool to complete the task." in prompt
    assert "Task parameters:" in prompt
    assert "- calendar_id: primary" in prompt
    assert "- time_min: 2026-03-20T00:00:00-04:00" in prompt
    assert "integration_kind" not in prompt
    assert "resource_kind" not in prompt
    assert "{" not in prompt
    assert "}" not in prompt


def test_google_bridge_calendar_schema_requires_local_time_bounds():
    schema = build_google_bridge_args_schema()

    time_min_description = schema["properties"]["time_min"]["description"]
    time_max_description = schema["properties"]["time_max"]["description"]
    time_zone_description = schema["properties"]["time_zone"]["description"]
    query_description = schema["properties"]["query"]["description"]
    message_id_description = schema["properties"]["message_id"]["description"]
    event_id_description = schema["properties"]["event_id"]["description"]
    subject_description = schema["properties"]["subject"]["description"]
    start_description = schema["properties"]["start"]["description"]
    action_kind_description = schema["properties"]["action_kind"]["description"]
    resource_kind_description = schema["properties"]["resource_kind"]["description"]
    calendar_id_description = schema["properties"]["calendar_id"]["description"]
    to_description = schema["properties"]["to"]["description"]
    cc_description = schema["properties"]["cc"]["description"]
    bcc_description = schema["properties"]["bcc"]["description"]
    body_description = schema["properties"]["body"]["description"]
    thread_id_description = schema["properties"]["thread_id"]["description"]
    draft_id_description = schema["properties"]["draft_id"]["description"]
    filter_id_description = schema["properties"]["filter_id"]["description"]
    criteria_description = schema["properties"]["criteria"]["description"]
    action_description = schema["properties"]["action"]["description"]
    dry_run_description = schema["properties"]["dry_run"]["description"]
    preview_max_results_description = schema["properties"]["preview_max_results"]["description"]

    assert "local time" in time_min_description
    assert "GMT or Zulu" in time_min_description
    assert "EST/EDT" in time_min_description
    assert "local time" in time_max_description
    assert "GMT or Zulu" in time_max_description
    assert "EST/EDT" in time_max_description
    assert "America/New_York" in time_zone_description
    account_scope_description = schema["properties"]["account_scope"]["description"]
    assert "generic google_bridge query language" in query_description.lower()
    assert "and, or, not" in query_description.lower()
    assert "field contains clauses" in query_description.lower()
    assert "from:(dsmith@aol.com or dsmyth@aol.com)" in query_description.lower()
    assert "to:(sktennis7@gmail.com or kissinger.scott@gmail.com)" in query_description.lower()
    assert "supported query fields vary by surface" in query_description.lower()
    assert "gmail list/read supports from, to, subject, label_ids, include_read, in, is, newer_than, and older_than" in query_description.lower()
    assert "gmail settings filters use the nested criteria object instead of query" in query_description.lower()
    assert "calendar list/read supports q" in query_description.lower()
    assert "name contains filters for filename searches" in query_description.lower()
    assert "q:(team sync or planning)" in query_description.lower()
    assert "message_id" in query_description.lower() or "message id" in query_description.lower()
    assert "from:airbnb.com" in query_description.lower()
    assert "exact sender" in query_description.lower()
    assert "sender-domain" in query_description.lower()
    assert "subject search" in query_description.lower()
    assert "fan out across every active connected account" in account_scope_description.lower()
    assert "every connected gmail inbox or calendar" in account_scope_description.lower()
    assert "account_email" in message_id_description
    assert "fetch subject, sender, snippet, and metadata" in message_id_description
    assert "account_email" in event_id_description
    assert "draft/send" in subject_description
    assert "draft/send" in to_description
    assert "draft/send" in cc_description
    assert "draft/send" in bcc_description
    assert "draft/send" in body_description
    assert "draft updates" in thread_id_description
    assert "send-from-draft" in draft_id_description
    assert "delete" in action_kind_description
    assert "trash" in action_kind_description
    assert "create" in action_kind_description
    assert "update" in action_kind_description
    assert "read, list, draft, send, create, update, delete" in action_kind_description
    assert "gmail settings supports filter list/create/update/delete workflows" in resource_kind_description.lower()
    assert "all calendars" in calendar_id_description.lower()
    assert "supports q for event search terms" in calendar_id_description.lower()
    assert "local time" in start_description
    assert "gmail settings filter identifier" in filter_id_description.lower()
    assert "gmail settings filter criteria object" in criteria_description.lower()
    assert "gmail settings filter action object" in action_description.lower()
    assert "preview a gmail settings filter create or update operation" in dry_run_description.lower()
    assert "preview messages to include per preview clause" in preview_max_results_description.lower()
    assert schema["properties"]["max_results"]["default"] == 20
    assert "maximum" not in schema["properties"]["max_results"]
    assert "operation" not in schema["required"]
    assert "operation" not in schema["properties"]["steps"]["items"]["required"]
    assert "compiled by the bridge planner" in query_description.lower()
    assert "calendar list/read supports q" in query_description.lower()
    gmail_delete_examples = [
        example
        for example in GOOGLE_BRIDGE_TOOL_EXAMPLES
        if example.get("resource_kind") == "gmail" and example.get("action_kind") == "delete"
    ]
    gmail_read_examples = [
        example
        for example in GOOGLE_BRIDGE_TOOL_EXAMPLES
        if example.get("resource_kind") == "gmail" and example.get("action_kind") == "read"
    ]
    assert any(
        example.get("query") == 'from:info@airbnb.com OR from:airbnb.com OR subject:("Airbnb")'
        and example.get("account_scope") == "primary"
        for example in gmail_read_examples
    )
    assert any(
        example.get("query") == 'from:info@airbnb.com OR from:airbnb.com OR subject:("Airbnb")'
        and example.get("account_scope") == "all"
        for example in gmail_read_examples
    )
    assert any(
        example.get("query") == "from:(dsmith@aol.com OR dsmyth@aol.com)"
        and example.get("account_scope") == "primary"
        for example in gmail_read_examples
    )
    assert any(
        example.get("query") == "to:(sktennis7@gmail.com OR kissinger.scott@gmail.com)"
        and example.get("account_scope") == "primary"
        for example in gmail_read_examples
    )
    assert any(
        example.get("query") == "subject:(invoice OR receipt) AND NOT label_ids:promotions"
        and example.get("account_scope") == "primary"
        for example in gmail_read_examples
    )
    calendar_read_examples = [
        example
        for example in GOOGLE_BRIDGE_TOOL_EXAMPLES
        if example.get("resource_kind") == "calendar" and example.get("action_kind") == "read"
    ]
    assert any(
        example.get("query") == "q:(team sync OR planning)"
        and example.get("calendar_id") == "primary"
        for example in calendar_read_examples
    )
    assert any(example.get("query") == "subject:(\"Airbnb\")" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("query") == "subject:(\"Airbnb\")" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)
    assert any(example.get("query") == "from:info@airbnb.com" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("query") == "from:info@airbnb.com" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)
    assert any(example.get("query") == "from:airbnb.com" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("query") == "from:airbnb.com" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "subject:(\"Airbnb\")" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "subject:(\"Airbnb\")" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "from:info@airbnb.com" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "from:info@airbnb.com" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "from:airbnb.com" and example.get("operation") == "trash" for example in gmail_delete_examples)
    assert any(example.get("account_scope") == "all" and example.get("query") == "from:airbnb.com" and example.get("operation") == "delete" and example.get("delete_mode") == "delete" for example in gmail_delete_examples)

    gmail_settings_examples = [
        example
        for example in GOOGLE_BRIDGE_TOOL_EXAMPLES
        if example.get("resource_kind") == "gmail_settings"
    ]
    assert any(example.get("operation") == "list" and example.get("action_kind") == "read" for example in gmail_settings_examples)
    assert any(example.get("operation") == "create" and example.get("action_kind") == "create" for example in gmail_settings_examples)
    assert any(example.get("dry_run") is True and example.get("preview_max_results") == 3 for example in gmail_settings_examples)
    assert any(example.get("operation") == "update" and example.get("action_kind") == "update" for example in gmail_settings_examples)
    assert any(example.get("operation") == "delete" and example.get("action_kind") == "delete" for example in gmail_settings_examples)


def test_execute_google_task_supports_drive_docs_and_sheets_steps(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {"drive_q": None, "export_calls": [], "sheet_range": None}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_drive_files(self, *, q: str = "", page_size: int = 20, page_token: str = "", include_all_drives: bool = True):
            captured["drive_q"] = q
            return {
                "files": [{"id": "drive-1", "name": "Drive File", "mimeType": "application/pdf"}],
                "nextPageToken": "",
            }

        def get_document(self, document_id: str):
            return {
                "documentId": document_id,
                "title": "Doc Title",
                "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Doc body text"}}]}}]},
            }

        def get_sheet_values(self, spreadsheet_id: str, *, range_name: str = ""):
            captured["sheet_range"] = range_name
            return {
                "spreadsheetId": spreadsheet_id,
                "range": range_name,
                "values": [["A1", "B1"], ["A2", "B2"]],
            }

        def export_drive_file(self, file_id: str, mime_type: str) -> bytes:
            captured["export_calls"].append((file_id, mime_type))
            return b"exported-bytes"

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "steps": [
                {
                    "resource_kind": "drive",
                    "action_kind": "read",
                    "operation": "list",
                    "query": 'mime_type:application/vnd.google-apps.document',
                    "max_results": 5,
                },
                {
                    "resource_kind": "docs",
                    "action_kind": "read",
                    "operation": "read",
                    "file_id": "doc-123",
                },
                {
                    "resource_kind": "sheets",
                    "action_kind": "export",
                    "operation": "export",
                    "file_id": "sheet-123",
                    "export_mime_type": "text/csv",
                },
            ],
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["drive_q"] == 'mime_type:application/vnd.google-apps.document'
    assert captured["sheet_range"] is None
    assert captured["export_calls"] == [("sheet-123", "text/csv")]
    assert result["steps"][0]["resource_kind"] == "drive"
    assert result["steps"][1]["resource_kind"] == "docs"
    assert result["steps"][2]["resource_kind"] == "sheets"
    assert result["steps"][0]["result"]["files"][0]["id"] == "drive-1"
    assert result["steps"][1]["result"]["documentId"] == "doc-123"
    assert result["steps"][2]["result"]["content_text"] == "exported-bytes"


def test_execute_google_task_passes_drive_contains_query_to_client(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_drive_files(self, *, q: str = "", page_size: int = 20, page_token: str = "", include_all_drives: bool = True):
            captured["q"] = q
            captured["page_size"] = page_size
            return {"files": [{"id": "drive-1", "name": "README.md", "mimeType": "text/plain"}], "nextPageToken": ""}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "drive",
            "action_kind": "read",
            "operation": "list",
            "query": "name contains 'README'",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["q"] == "name contains 'README'"
    assert captured["page_size"] == 5
    assert result["result"]["query"] == "name contains 'README'"
    assert result["result"]["files"][0]["id"] == "drive-1"


def test_execute_google_task_canonicalizes_drive_mime_type_alias(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_drive_files(self, *, q: str = "", page_size: int = 20, page_token: str = "", include_all_drives: bool = True):
            captured["q"] = q
            return {"files": [{"id": "drive-1", "name": "README.md", "mimeType": "application/vnd.google-apps.document"}], "nextPageToken": ""}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "drive",
            "action_kind": "read",
            "operation": "list",
            "query": "mimeType = 'application/vnd.google-apps.document'",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["q"] == "mimeType = 'application/vnd.google-apps.document'"
    assert result["result"]["query"] == "mimeType = 'application/vnd.google-apps.document'"


def test_execute_google_task_canonicalizes_drive_date_and_boolean_aliases(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_drive_files(self, *, q: str = "", page_size: int = 20, page_token: str = "", include_all_drives: bool = True):
            captured["q"] = q
            return {"files": [{"id": "drive-1", "name": "README.md", "mimeType": "application/vnd.google-apps.document"}], "nextPageToken": ""}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "drive",
            "action_kind": "read",
            "operation": "list",
            "query": "modifiedTime >= '2024-01-01T00:00:00Z' AND createdTime < '2024-02-01T00:00:00Z' AND trashed = false",
            "max_results": 5,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["q"] == "modifiedTime >= '2024-01-01T00:00:00Z' and createdTime < '2024-02-01T00:00:00Z' and trashed = false"
    assert result["result"]["query"] == "modifiedTime >= '2024-01-01T00:00:00Z' and createdTime < '2024-02-01T00:00:00Z' and trashed = false"


def test_execute_google_task_supports_people_list_search_and_read(monkeypatch):
    workspace, user, account = _make_account()

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connection):
            self.connection = connection

        def list_people_connections(
            self,
            *,
            person_fields: str,
            page_size: int = 100,
            page_token: str = "",
            sort_order: str = "",
            request_sync_token: bool = False,
            sync_token: str = "",
            sources: list[str] | None = None,
        ):
            captured["list"] = {
                "person_fields": person_fields,
                "page_size": page_size,
                "page_token": page_token,
                "sort_order": sort_order,
                "request_sync_token": request_sync_token,
                "sync_token": sync_token,
                "sources": sources,
            }
            return {
                "connections": [
                    {
                        "resourceName": "people/c123",
                        "names": [{"displayName": "Scott Kissinger"}],
                        "emailAddresses": [{"value": "scott@example.com"}],
                    }
                ],
                "nextPageToken": "",
                "nextSyncToken": "sync-1",
                "totalPeople": 1,
            }

        def search_people_contacts(
            self,
            *,
            query: str,
            read_mask: str,
            page_size: int = 10,
            page_token: str = "",
            sources: list[str] | None = None,
        ):
            captured["search"] = {
                "query": query,
                "read_mask": read_mask,
                "page_size": page_size,
                "page_token": page_token,
                "sources": sources,
            }
            return {
                "results": [
                    {
                        "person": {
                            "resourceName": "people/c456",
                            "names": [{"displayName": "Scott Kissinger"}],
                            "emailAddresses": [{"value": "scott@example.com"}],
                        }
                    }
                ],
                "totalItems": 1,
            }

        def get_people(self, resource_name: str, *, person_fields: str, sources: list[str] | None = None):
            captured["read"] = {"resource_name": resource_name, "person_fields": person_fields, "sources": sources}
            return {
                "resourceName": resource_name,
                "names": [{"displayName": "Scott Kissinger"}],
                "emailAddresses": [{"value": "scott@example.com"}],
            }

        def create_people_contact(self, *, person: dict, person_fields: str):
            captured["create"] = {"person": person, "person_fields": person_fields}
            return {
                "resourceName": "people/c789",
                "names": [{"displayName": "New Contact"}],
                "emailAddresses": [{"value": "new@example.com"}],
            }

        def update_people_contact(self, resource_name: str, *, person: dict, person_fields: str, update_person_fields: str):
            captured["update"] = {
                "resource_name": resource_name,
                "person": person,
                "person_fields": person_fields,
                "update_person_fields": update_person_fields,
            }
            return {
                "resourceName": resource_name,
                "etag": "etag-1",
                "metadata": {"sources": [{"type": "CONTACT", "etag": "source-etag-1"}]},
                "names": [{"displayName": "Updated Contact"}],
                "emailAddresses": [{"value": "updated@example.com"}],
            }

        def delete_people_contact(self, resource_name: str):
            captured["delete"] = {"resource_name": resource_name}
            return {}

    monkeypatch.setattr("google_bridge.services.bridge.GoogleBridgeClient", FakeClient)

    list_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "read",
            "operation": "list",
            "person_fields": "names,emailAddresses",
            "page_size": 25,
            "request_sync_token": True,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    search_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "read",
            "operation": "search",
            "query": "Scott Kissinger",
            "read_mask": "names,emailAddresses",
            "page_size": 10,
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    read_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "read",
            "operation": "read",
            "resource_name": "people/c456",
            "person_fields": "names,emailAddresses",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    create_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "create",
            "operation": "create",
            "account_scope": "primary",
            "person": {
                "names": [{"givenName": "New", "familyName": "Contact"}],
                "emailAddresses": [{"value": "new@example.com"}],
            },
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    update_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "update",
            "operation": "update",
            "account_scope": "primary",
            "resource_name": "people/c789",
            "person_fields": "names,emailAddresses,metadata",
            "update_person_fields": "emailAddresses",
            "person": {
                "resourceName": "people/c789",
                "etag": "etag-1",
                "metadata": {"sources": [{"type": "CONTACT"}]},
                "emailAddresses": [{"value": "updated@example.com"}],
            },
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    delete_result = execute_google_task(
        payload={
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "delete",
            "operation": "delete",
            "account_scope": "primary",
            "resource_name": "people/c789",
        },
        workspace=workspace,
        owner=user,
        account=account,
    )

    assert captured["list"]["person_fields"] == "names,emailAddresses"
    assert captured["list"]["page_size"] == 25
    assert captured["search"]["query"] == "Scott Kissinger"
    assert captured["search"]["read_mask"] == "names,emailAddresses"
    assert captured["read"]["resource_name"] == "people/c456"
    assert captured["create"]["person_fields"] == "names,emailAddresses,phoneNumbers,metadata"
    assert captured["create"]["person"]["names"][0]["givenName"] == "New"
    assert captured["update"]["resource_name"] == "people/c789"
    assert captured["update"]["update_person_fields"] == "emailAddresses"
    assert captured["delete"]["resource_name"] == "people/c789"
    assert "Returned 1 Google contacts" in list_result["summary_text"]
    assert "Returned 1 Google contacts search results" in search_result["summary_text"]
    assert "Google contact read" in read_result["summary_text"]
    assert "Google contact created" in create_result["summary_text"]
    assert "Google contact updated" in update_result["summary_text"]
    assert "Google contact deleted" in delete_result["summary_text"]
    assert list_result["result"]["connections"][0]["resourceName"] == "people/c123"
    assert search_result["result"]["results"][0]["person"]["resourceName"] == "people/c456"
    assert read_result["result"]["resourceName"] == "people/c456"
    assert create_result["result"]["resourceName"] == "people/c789"
    assert update_result["result"]["resourceName"] == "people/c789"
    assert delete_result["result"]["deleted"] is True


def test_normalize_google_payload_allows_people_search_action_kind():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "search",
            "query": "Scott Kissinger",
            "read_mask": "names,emailAddresses",
            "page_size": 5,
        }
    )

    assert payload["resource_kind"] == "people"
    assert payload["action_kind"] == "search"
    assert payload["operation"] == "search"
    assert payload["query"] == "Scott Kissinger"
    assert payload["read_mask"] == "names,emailAddresses"


def test_execute_google_task_rejects_people_writes_for_account_scope_all():
    workspace, user, account = _make_account()
    GoogleAccount.objects.create(
        workspace=workspace,
        owner=user,
        google_subject="sub-456",
        email="user2@example.com",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/contacts",
        ],
        token_expires_at=timezone.now() + timedelta(hours=1),
        is_active=True,
    )

    with pytest.raises(
        GoogleBridgeTaskError,
        match="People write tasks require a specific connected account",
    ):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "people",
                "action_kind": "create",
                "operation": "create",
                "account_scope": "all",
                "person": {
                    "names": [{"givenName": "New", "familyName": "Contact"}],
                },
            },
            workspace=workspace,
            owner=user,
            account=account,
        )


def test_normalize_google_payload_allows_people_write_actions():
    create_payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "create",
            "account_scope": "primary",
            "person": {
                "names": [{"givenName": "New", "familyName": "Contact"}],
            },
        }
    )
    update_payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "update",
            "account_scope": "primary",
            "resource_name": "people/c789",
            "update_person_fields": "emailAddresses",
            "person": {
                "resourceName": "people/c789",
                "etag": "etag-1",
                "metadata": {"sources": [{"type": "CONTACT"}]},
                "emailAddresses": [{"value": "updated@example.com"}],
            },
        }
    )
    delete_payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "people",
            "action_kind": "delete",
            "account_scope": "primary",
            "resource_name": "people/c789",
        }
    )

    assert create_payload["operation"] == "create"
    assert update_payload["operation"] == "update"
    assert delete_payload["operation"] == "delete"
    assert create_payload["person"]["names"][0]["givenName"] == "New"
    assert update_payload["update_person_fields"] == "emailAddresses"
    assert delete_payload["resource_name"] == "people/c789"


def test_execute_google_task_requires_people_update_etag():
    workspace, user, account = _make_account()

    with pytest.raises(
        GoogleBridgeTaskError,
        match="requires person.etag or person.metadata.sources\\[\\]\\.etag",
    ):
        execute_google_task(
            payload={
                "integration_kind": "google",
                "resource_kind": "people",
                "action_kind": "update",
                "operation": "update",
                "account_scope": "primary",
                "resource_name": "people/c789",
                "update_person_fields": "emailAddresses",
                "person": {
                    "resourceName": "people/c789",
                    "metadata": {"sources": [{"type": "CONTACT"}]},
                    "emailAddresses": [{"value": "updated@example.com"}],
                },
            },
            workspace=workspace,
            owner=user,
            account=account,
        )


def test_normalize_google_payload_allows_drive_list_query_without_file_id():
    payload = normalize_google_payload(
        {
            "integration_kind": "google",
            "resource_kind": "drive",
            "action_kind": "read",
            "operation": "list",
            "query": "name contains 'README'",
            "account_scope": "primary",
        }
    )

    assert payload["resource_kind"] == "drive"
    assert payload["action_kind"] == "read"
    assert payload["operation"] == "list"
    assert payload["query"] == "name contains 'README'"
    assert payload["file_id"] == ""
