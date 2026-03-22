from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone

from core.services.timezones import get_local_timezone_name
from google_bridge.models import GoogleAccount
from google_bridge.services.client import GoogleBridgeClient


class GoogleBridgeTaskError(RuntimeError):
    pass


def build_google_task_objective(payload: dict | None) -> tuple[str, str]:
    normalized = normalize_google_payload(payload)
    objective = f"Complete the Google bridge task for {normalized['resource_kind']} {normalized['operation']}."
    prompt = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return objective, prompt


def execute_google_task(*, payload: dict, workspace=None, owner=None, account: GoogleAccount | None = None) -> dict[str, object]:
    normalized = normalize_google_payload(payload)
    steps = list(normalized.get("steps") or [])
    if not steps:
        steps = [normalized]

    step_results: list[dict[str, object]] = []
    all_accounts: list[dict[str, object]] = []
    all_accounts_seen: set[tuple[str, str, str]] = set()
    for index, step in enumerate(steps, start=1):
        step_result = _execute_google_step(
            step,
            workspace=workspace,
            owner=owner,
            account=account,
        )
        step_results.append(
            {
                "index": index,
                "resource_kind": step_result["resource_kind"],
                "action_kind": step_result["action_kind"],
                "operation": step_result["operation"],
                "summary_text": step_result["summary_text"],
                "result": step_result["result"],
                "accounts": step_result["accounts"],
            }
        )
        for item in step_result["accounts"]:
            account_key = (
                str(item.get("workspace_id") or ""),
                str(item.get("owner_id") or ""),
                str(item.get("google_subject") or ""),
            )
            if account_key in all_accounts_seen:
                continue
            all_accounts_seen.add(account_key)
            all_accounts.append(dict(item))

    if len(step_results) == 1:
        result = dict(step_results[0]["result"]) if isinstance(step_results[0]["result"], dict) else step_results[0]["result"]
        summary_text = str(step_results[0]["summary_text"])
    else:
        result = {"steps": step_results}
        summary_text = " ".join(f"{item['index']}. {item['summary_text']}" for item in step_results if str(item.get("summary_text") or "").strip())

    return {
        "ok": True,
        "integration_kind": "google",
        "resource_kind": str(step_results[0]["resource_kind"]) if step_results else "",
        "action_kind": str(step_results[0]["action_kind"]) if step_results else "",
        "operation": str(step_results[0]["operation"]) if step_results else "",
        "summary_text": summary_text,
        "result": result,
        "steps": step_results,
        "accounts": all_accounts,
    }


def normalize_google_payload(payload: dict | None) -> dict[str, object]:
    raw = dict(payload or {})
    raw["integration_kind"] = str(raw.get("integration_kind") or "google").strip().lower()
    raw["google_subject"] = str(raw.get("google_subject") or "").strip()
    raw["email"] = str(raw.get("email") or "").strip()
    steps = [
        _normalize_google_step(step, defaults=raw, step_index=index)
        for index, step in enumerate(list(raw.get("steps") or []), start=1)
    ]
    raw["steps"] = steps
    if steps:
        raw.update(dict(steps[0]))
    else:
        normalized_step = _normalize_google_step(raw, defaults=raw, step_index=1)
        raw.update(normalized_step)
    return raw


def resolve_google_account(*, workspace=None, owner=None) -> GoogleAccount | None:
    queryset = GoogleAccount.objects.filter(is_active=True)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    return queryset.order_by("-last_synced_at", "-updated_at").first()


def resolve_google_accounts(
    *,
    workspace=None,
    owner=None,
    account_scope: str = "primary",
    google_subject: str = "",
    email: str = "",
):
    queryset = GoogleAccount.objects.filter(is_active=True)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    if google_subject:
        queryset = queryset.filter(google_subject=google_subject)
    if email:
        queryset = queryset.filter(email=email)
    if account_scope == "all":
        return queryset.order_by("-last_synced_at", "-updated_at", "email", "google_subject")
    return queryset.order_by("-last_synced_at", "-updated_at")[:1]


def _execute_google_task_for_account(
    connection: GoogleAccount,
    normalized: dict[str, object],
    resource_kind: str,
    operation: str,
) -> tuple[dict[str, object], str]:
    client = GoogleBridgeClient(connection)
    if resource_kind == "gmail":
        if operation == "read":
            message_id = str(normalized.get("message_id") or "").strip()
            if message_id:
                data = client.get_gmail_message(message_id)
                summary_text = _summarize_gmail_message(data)
            else:
                include_read = bool(normalized.get("_gmail_list_include_read"))
                unread_only = not include_read
                data = _execute_gmail_list_for_account(
                    client=client,
                    normalized=normalized,
                    unread_only=unread_only,
                )
                summary_text = _summarize_gmail_list(data, unread_only=unread_only)
        elif operation == "create":
            recipients = _normalize_string_list(normalized.get("to"))
            if not recipients:
                raise GoogleBridgeTaskError("gmail draft tasks require at least one recipient in to.")
            data = client.create_gmail_draft(
                to=recipients,
                subject=str(normalized.get("subject") or "").strip(),
                body=str(normalized.get("body") or "").strip(),
                cc=_normalize_string_list(normalized.get("cc")),
                bcc=_normalize_string_list(normalized.get("bcc")),
                thread_id=str(normalized.get("thread_id") or "").strip(),
            )
            summary_text = _summarize_gmail_draft(data)
        elif operation == "send":
            draft_id = str(normalized.get("draft_id") or "").strip()
            if draft_id:
                data = client.send_gmail_draft(draft_id)
            else:
                recipients = _normalize_string_list(normalized.get("to"))
                if not recipients:
                    raise GoogleBridgeTaskError("gmail send tasks require at least one recipient in to or a draft_id.")
                data = client.send_gmail_message(
                    to=recipients,
                    subject=str(normalized.get("subject") or "").strip(),
                    body=str(normalized.get("body") or "").strip(),
                    cc=_normalize_string_list(normalized.get("cc")),
                    bcc=_normalize_string_list(normalized.get("bcc")),
                    thread_id=str(normalized.get("thread_id") or "").strip(),
                )
            summary_text = _summarize_gmail_send(data)
        elif operation in {"trash", "delete"}:
            message_id = str(normalized.get("message_id") or "").strip()
            deleted_ids: list[str] = []
            if not message_id:
                query = str(normalized.get("query") or "").strip()
                queries = _expand_gmail_query_clauses(query)
                if not query:
                    raise GoogleBridgeTaskError(
                        "gmail delete tasks require message_id or a Gmail query. "
                        "For bulk cleanup, use a sender-domain query like from:airbnb.com and the bridge will trash or delete every matching message."
                    )
                matches = _collect_gmail_messages_for_queries(
                    client,
                    queries=queries,
                    label_ids=list(normalized.get("label_ids") or []),
                )
                for match in matches:
                    resolved_id = str(match.get("id") or "").strip()
                    if not resolved_id:
                        continue
                    deleted_ids.append(resolved_id)
                    if operation == "trash":
                        client.trash_gmail_message(resolved_id)
                    else:
                        client.delete_gmail_message(resolved_id)
                if deleted_ids:
                    data = {"deleted_message_ids": deleted_ids, "matched_queries": queries}
                    summary_text = (
                        _summarize_gmail_bulk_trash(deleted_ids)
                        if operation == "trash"
                        else _summarize_gmail_bulk_delete(deleted_ids)
                    )
                else:
                    data = {"deleted_message_ids": [], "matched_queries": queries}
                    summary_text = _summarize_gmail_no_matches_for_cleanup(
                        queries=queries,
                        accounts=[{
                            "google_subject": normalized.get("google_subject") or "",
                            "email": normalized.get("email") or "",
                        }],
                    )
            else:
                if operation == "trash":
                    data = client.trash_gmail_message(message_id)
                    summary_text = _summarize_gmail_trash(data, message_id=message_id)
                else:
                    data = client.delete_gmail_message(message_id)
                    summary_text = _summarize_gmail_delete(data, message_id=message_id)
        else:
            include_read = bool(normalized.get("_gmail_list_include_read"))
            unread_only = not include_read
            data = _execute_gmail_list_for_account(
                client=client,
                normalized=normalized,
                unread_only=unread_only,
            )
            summary_text = _summarize_gmail_list(data, unread_only=unread_only)
    elif resource_kind == "calendar":
        if operation == "read":
            event_id = str(normalized.get("event_id") or "").strip()
            calendar_id = str(normalized.get("calendar_id") or "primary").strip() or "primary"
            if not event_id:
                raise GoogleBridgeTaskError("calendar read tasks require event_id.")
            data = client.get_calendar_event(calendar_id=calendar_id, event_id=event_id)
            summary_text = _summarize_calendar_event(data)
        elif operation == "list":
            time_min = _normalize_calendar_timestamp(normalized.get("time_min"))
            time_max = _normalize_calendar_timestamp(normalized.get("time_max"))
            data, summary_text = _execute_calendar_list_for_account(
                client=client,
                normalized=normalized,
                time_min=time_min,
                time_max=time_max,
            )
        elif operation == "create":
            calendar_id = str(normalized.get("calendar_id") or "primary").strip() or "primary"
            summary = str(normalized.get("summary") or "").strip()
            if not summary:
                raise GoogleBridgeTaskError("calendar create tasks require summary.")
            time_zone_name = _normalize_calendar_timezone_name(normalized.get("time_zone"))
            start = _normalize_calendar_event_timestamp(normalized.get("start"), time_zone_name)
            end = _normalize_calendar_event_timestamp(normalized.get("end"), time_zone_name)
            if not start:
                raise GoogleBridgeTaskError("calendar create tasks require start.")
            if not end:
                raise GoogleBridgeTaskError("calendar create tasks require end.")
            data = client.create_calendar_event(
                calendar_id=calendar_id,
                summary=summary,
                description=str(normalized.get("description") or "").strip(),
                location=str(normalized.get("location") or "").strip(),
                start=start,
                end=end,
                attendees=_normalize_string_list(normalized.get("attendees")),
                send_updates=_normalize_calendar_send_updates(normalized.get("send_updates")),
            )
            summary_text = _summarize_calendar_mutation(data, action="created")
        elif operation == "update":
            calendar_id = str(normalized.get("calendar_id") or "primary").strip() or "primary"
            event_id = str(normalized.get("event_id") or "").strip()
            if not event_id:
                raise GoogleBridgeTaskError("calendar update tasks require event_id.")
            time_zone_name = _normalize_calendar_timezone_name(normalized.get("time_zone"))
            payload = _build_calendar_write_fields(normalized, time_zone_name)
            if not payload:
                raise GoogleBridgeTaskError(
                    "calendar update tasks require at least one field to update."
                )
            data = client.update_calendar_event(
                calendar_id=calendar_id,
                event_id=event_id,
                summary=str(payload.get("summary") or "").strip(),
                description=str(payload.get("description") or "").strip(),
                location=str(payload.get("location") or "").strip(),
                start=dict(payload.get("start") or {}),
                end=dict(payload.get("end") or {}),
                attendees=_normalize_string_list(payload.get("attendees")),
                send_updates=_normalize_calendar_send_updates(normalized.get("send_updates")),
            )
            summary_text = _summarize_calendar_mutation(data, action="updated")
        elif operation == "delete":
            calendar_id = str(normalized.get("calendar_id") or "primary").strip() or "primary"
            event_id = str(normalized.get("event_id") or "").strip()
            if not event_id:
                raise GoogleBridgeTaskError("calendar delete tasks require event_id.")
            data = client.delete_calendar_event(
                calendar_id=calendar_id,
                event_id=event_id,
                send_updates=_normalize_calendar_send_updates(normalized.get("send_updates")),
            )
            summary_text = _summarize_calendar_delete(event_id)
        else:
            raise GoogleBridgeTaskError(
                f"Unsupported Google calendar operation '{operation}'."
            )
    else:
        raise GoogleBridgeTaskError(f"Unsupported Google resource kind '{resource_kind}'.")
    return data, summary_text


def _execute_calendar_list_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
    time_min: str,
    time_max: str,
) -> tuple[dict[str, object], str]:
    max_results = int(normalized.get("max_results") or 20)
    calendar_id = str(normalized.get("calendar_id") or "").strip()
    explicit_calendar_id = bool(normalized.get("_calendar_id_explicit"))
    if calendar_id and calendar_id != "all" and (explicit_calendar_id or calendar_id != "primary"):
        data = client.list_calendar_events(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )
        items = [
            {
                **dict(item),
                "calendar_id": calendar_id,
            }
            for item in list(data.get("items") or [])
        ]
        data = dict(data)
        data["items"] = items
        data["calendars"] = [
            {
                "id": calendar_id,
                "summary": calendar_id,
                "selected": True,
            }
        ]
        return data, _summarize_calendar_list(data)

    calendar_list = client.list_calendar_list()
    calendars = [
        dict(item)
        for item in list(calendar_list.get("items") or [])
        if not bool(item.get("deleted"))
    ]
    if not calendars:
        calendars = [{"id": "primary", "summary": "primary", "selected": True}]

    merged_items: list[dict[str, object]] = []
    calendar_summaries: list[dict[str, object]] = []
    for calendar in calendars:
        calendar_id_value = str(calendar.get("id") or "").strip()
        if not calendar_id_value:
            continue
        events = client.list_calendar_events(
            calendar_id=calendar_id_value,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )
        calendar_summary = str(calendar.get("summary") or calendar_id_value).strip() or calendar_id_value
        calendar_summaries.append(
            {
                "id": calendar_id_value,
                "summary": calendar_summary,
                "primary": bool(calendar.get("primary")),
                "selected": bool(calendar.get("selected")),
            }
        )
        for item in list(events.get("items") or []):
            merged_items.append(
                {
                    **dict(item),
                    "calendar_id": calendar_id_value,
                    "calendar_summary": calendar_summary,
                }
            )

    merged_items.sort(key=_calendar_event_sort_key)
    data = {
        "kind": "calendar#events",
        "items": merged_items,
        "calendars": calendar_summaries,
        "resultSizeEstimate": len(merged_items),
    }
    return data, _summarize_calendar_list(data)


def _execute_google_step(
    step: dict[str, object],
    *,
    workspace=None,
    owner=None,
    account: GoogleAccount | None = None,
) -> dict[str, object]:
    normalized = _normalize_google_step(step, defaults=step)
    account_scope = str(normalized.get("account_scope") or "primary")
    email = str(normalized.get("email") or "").strip()
    google_subject = str(normalized.get("google_subject") or "").strip()
    if not email and "@" in google_subject:
        email = google_subject
    selected_accounts = list(
        resolve_google_accounts(
            workspace=workspace,
            owner=owner,
            account_scope=account_scope,
            google_subject=google_subject,
            email=email,
        )
    )
    if not selected_accounts and email:
        selected_accounts = list(
            resolve_google_accounts(
                workspace=workspace,
                owner=owner,
                account_scope=account_scope,
                email=email,
            )
        )
    if not selected_accounts and google_subject:
        selected_accounts = list(
            resolve_google_accounts(
                workspace=workspace,
                owner=owner,
                account_scope=account_scope,
                google_subject=google_subject,
            )
        )
    if account is not None:
        selected_accounts = [account]
    if not selected_accounts:
        raise GoogleBridgeTaskError("No active Google account connection is available.")

    resource_kind = str(normalized["resource_kind"])
    operation = str(normalized["operation"])
    if account is None and resource_kind == "gmail" and operation in {"create", "send"}:
        if not str(normalized.get("email") or "").strip() and not str(normalized.get("google_subject") or "").strip():
            candidate_count = GoogleAccount.objects.filter(is_active=True)
            if workspace is not None:
                candidate_count = candidate_count.filter(workspace=workspace)
            if owner is not None:
                candidate_count = candidate_count.filter(owner=owner)
            if candidate_count.count() != 1:
                raise GoogleBridgeTaskError(
                    "Gmail write tasks require a specific connected account. Use email or google_subject to target the account explicitly."
                )
    if account is None and resource_kind == "calendar" and operation in {"create", "update", "delete"}:
        if not str(normalized.get("email") or "").strip() and not str(normalized.get("google_subject") or "").strip():
            candidate_count = GoogleAccount.objects.filter(is_active=True)
            if workspace is not None:
                candidate_count = candidate_count.filter(workspace=workspace)
            if owner is not None:
                candidate_count = candidate_count.filter(owner=owner)
            if candidate_count.count() != 1:
                raise GoogleBridgeTaskError(
                    "Calendar write tasks require a specific connected account. Use email or google_subject to target the account explicitly."
                )
    if normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation in {"list", "read"} and not str(normalized.get("message_id") or "").strip():
        result, summary_text = _execute_merged_list_task(selected_accounts, normalized, resource_kind)
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation == "read":
        raise GoogleBridgeTaskError(
            "Google bridge read tasks require the specific account from the list result. "
            "Use the returned account_email or google_subject together with message_id or event_id."
        )
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and resource_kind == "gmail" and operation in {"trash", "delete"}:
        if str(normalized.get("message_id") or "").strip():
            raise GoogleBridgeTaskError(
                "Gmail delete-by-id tasks require a specific connected account. Use email or google_subject to target the account explicitly."
            )
        result, summary_text = _execute_merged_delete_task(selected_accounts, normalized, operation)
    else:
        connection = selected_accounts[0]
        result, summary_text = _execute_google_task_for_account(connection, normalized, resource_kind, operation)
        selected_accounts = [connection]

    for connection in selected_accounts:
        connection.last_synced_at = timezone.now()
        connection.last_error = ""
        connection.save(update_fields=["last_synced_at", "last_error", "updated_at"])

    return {
        "resource_kind": resource_kind,
        "action_kind": str(normalized["action_kind"]),
        "operation": operation,
        "summary_text": summary_text,
        "result": result,
        "accounts": [
            {
                "google_subject": connection.google_subject,
                "email": connection.email,
                "workspace_id": str(connection.workspace_id),
                "owner_id": str(connection.owner_id),
            }
            for connection in selected_accounts
        ],
    }


def _execute_merged_list_task(
    accounts: list[GoogleAccount],
    normalized: dict[str, object],
    resource_kind: str,
) -> tuple[dict[str, object], str]:
    merged_items: list[dict[str, object]] = []
    account_summaries: list[dict[str, object]] = []
    merged_calendars: list[dict[str, object]] = []
    unread_only = bool(normalized.get("_gmail_list_default_unread"))
    for connection in accounts:
        data, summary_text = _execute_google_task_for_account(connection, normalized, resource_kind, "list")
        account_summaries.append(
            {
                "google_subject": connection.google_subject,
                "email": connection.email,
                "summary_text": summary_text,
            }
        )
        if resource_kind == "gmail":
            items = list(data.get("messages") or [])
            for item in items:
                merged_items.append(
                    {
                        **dict(item),
                        "account_email": connection.email,
                        "account_google_subject": connection.google_subject,
                    }
                )
        else:
            items = list(data.get("items") or [])
            for calendar_item in list(data.get("calendars") or []):
                merged_calendars.append(
                    {
                        **dict(calendar_item),
                        "account_email": connection.email,
                        "account_google_subject": connection.google_subject,
                    }
                )
            for item in items:
                merged_items.append(
                    {
                        **dict(item),
                        "account_email": connection.email,
                        "account_google_subject": connection.google_subject,
                    }
                )

    if resource_kind == "gmail":
        max_results = int(normalized.get("max_results") or 20)
        merged_items.sort(key=_gmail_message_sort_key)
        merged_items = merged_items[:max_results]
        count_label = "unread Gmail messages" if unread_only else "Gmail messages"
        return (
            {"messages": merged_items, "resultSizeEstimate": len(merged_items), "accounts": account_summaries},
            f"Returned {len(merged_items)} {count_label} across {len(accounts)} accounts.",
        )

    return (
        {"items": merged_items, "calendars": merged_calendars, "accounts": account_summaries},
        f"Found {len(merged_items)} calendar events across {len(accounts)} accounts and {len(merged_calendars)} calendars.",
    )


def _execute_merged_delete_task(
    accounts: list[GoogleAccount],
    normalized: dict[str, object],
    operation: str,
) -> tuple[dict[str, object], str]:
    account_summaries: list[dict[str, object]] = []
    deleted_message_ids: list[str] = []
    queries = _expand_gmail_query_clauses(str(normalized.get("query") or "").strip())
    for connection in accounts:
        data, summary_text = _execute_google_task_for_account(
            connection,
            normalized,
            "gmail",
            operation,
        )
        if not list(data.get("deleted_message_ids") or []):
            account_summaries.append(
                {
                    "google_subject": connection.google_subject,
                    "email": connection.email,
                    "summary_text": "No Gmail messages matched this cleanup query.",
                }
            )
            continue

        account_summaries.append(
            {
                "google_subject": connection.google_subject,
                "email": connection.email,
                "summary_text": summary_text,
            }
        )
        deleted_message_ids.extend(list(data.get("deleted_message_ids") or []))

    if not deleted_message_ids:
        data = {
            "deleted_message_ids": [],
            "matched_queries": queries,
            "accounts": account_summaries,
        }
        summary_text = _summarize_gmail_no_matches_for_cleanup(queries=queries, accounts=account_summaries)
    else:
        if operation == "trash":
            summary_text = f"Trashed {len(deleted_message_ids)} Gmail messages across {len(accounts)} accounts."
        else:
            summary_text = f"Permanently deleted {len(deleted_message_ids)} Gmail messages across {len(accounts)} accounts."
    return (
        {
            "deleted_message_ids": deleted_message_ids,
            "matched_queries": queries,
            "accounts": account_summaries,
        },
        summary_text,
    )


def _execute_gmail_list_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
    unread_only: bool,
) -> dict[str, object]:
    max_results = int(normalized.get("max_results") or 20)
    queries = _expand_gmail_query_clauses(str(normalized.get("query") or "").strip())
    raw_messages = _collect_gmail_messages_for_queries(
        client,
        queries=queries,
        label_ids=list(normalized.get("label_ids") or []),
        max_results_per_query=max_results,
    )
    data = {"messages": raw_messages, "resultSizeEstimate": len(raw_messages)}
    data = _enrich_gmail_list_messages(client, data)
    enriched_messages = list(data.get("messages") or [])
    enriched_messages.sort(key=_gmail_message_sort_key)
    enriched_messages = enriched_messages[:max_results]
    data = dict(data)
    data["messages"] = enriched_messages
    data["resultSizeEstimate"] = len(enriched_messages)
    return data


def _normalize_google_step(step: dict | None, *, defaults: dict[str, object] | None = None, step_index: int = 1) -> dict[str, object]:
    base = dict(defaults or {})
    raw = dict(base)
    step_data = dict(step or {})
    raw.update(step_data)
    integration_kind = str(raw.get("integration_kind") or "google").strip().lower()
    resource_kind = str(raw.get("resource_kind") or "").strip().lower()
    action_kind = str(raw.get("action_kind") or "read").strip().lower()
    operation = str(raw.get("operation") or "list").strip().lower()
    account_scope = str(raw.get("account_scope") or "primary").strip().lower()
    if integration_kind != "google":
        raise GoogleBridgeTaskError(f"Google step {step_index} only accepts integration_kind=google.")
    if resource_kind not in {"gmail", "calendar"}:
        raise GoogleBridgeTaskError(f"Google step {step_index} requires resource_kind=gmail or calendar.")
    if account_scope not in {"primary", "all"}:
        raise GoogleBridgeTaskError(f"Google step {step_index} requires account_scope=primary or all.")
    raw["integration_kind"] = integration_kind
    raw["resource_kind"] = resource_kind
    raw["account_scope"] = account_scope
    raw["_calendar_id_explicit"] = "calendar_id" in step_data and bool(str(step_data.get("calendar_id") or "").strip())
    raw["_gmail_list_include_read"] = _normalize_bool(raw.get("include_read"))
    raw["max_results"] = int(raw.get("max_results") or 20)
    raw["label_ids"] = _normalize_string_list(raw.get("label_ids"))
    raw["query"] = str(raw.get("query") or "").strip()
    raw["calendar_id"] = str(raw.get("calendar_id") or "primary").strip() or "primary"
    raw["time_min"] = _normalize_calendar_timestamp(raw.get("time_min"))
    raw["time_max"] = _normalize_calendar_timestamp(raw.get("time_max"))
    raw["time_zone"] = _normalize_calendar_timezone_name(raw.get("time_zone"))
    raw["message_id"] = str(raw.get("message_id") or "").strip()
    raw["event_id"] = str(raw.get("event_id") or "").strip()
    raw["draft_id"] = str(raw.get("draft_id") or "").strip()
    raw["subject"] = str(raw.get("subject") or "").strip()
    raw["body"] = str(raw.get("body") or "").strip()
    raw["thread_id"] = str(raw.get("thread_id") or "").strip()
    raw["to"] = _normalize_string_list(raw.get("to"))
    raw["cc"] = _normalize_string_list(raw.get("cc"))
    raw["bcc"] = _normalize_string_list(raw.get("bcc"))
    raw["attendees"] = _normalize_string_list(raw.get("attendees"))
    raw["send_updates"] = _normalize_calendar_send_updates(raw.get("send_updates"))
    raw["google_subject"] = str(raw.get("google_subject") or "").strip()
    raw["email"] = str(raw.get("email") or "").strip()
    raw["delete_mode"] = str(raw.get("delete_mode") or "").strip().lower()
    if resource_kind == "gmail":
        query_text = str(raw.get("query") or "").strip()
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "draft", "send", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports read, draft, send, and delete actions for Gmail.")
        if operation == "list" and not raw["_gmail_list_include_read"] and not str(raw.get("query") or "").strip() and not list(raw.get("label_ids") or []):
            raw["query"] = "is:unread"
            raw["_gmail_list_default_unread"] = True
        if action_kind in {"read", "delete"} and query_text:
            _expand_gmail_query_clauses(query_text)
        if operation == "list" and action_kind == "draft":
            operation = "create"
        if operation == "list" and action_kind == "send":
            operation = "send"
        if operation == "list" and action_kind == "delete":
            operation = "trash"
        if action_kind == "read" and operation not in {"list", "read"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports list/read operations for Gmail reads.")
        if action_kind == "draft" and operation not in {"create"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=create for Gmail drafts.")
        if action_kind == "send" and operation not in {"send"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=send for Gmail sends.")
        if action_kind == "delete" and operation not in {"trash", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=trash or delete for Gmail delete workflows.")
        if action_kind == "delete":
            if raw["delete_mode"] in {"trash", "delete"}:
                operation = raw["delete_mode"]
            elif operation not in {"trash", "delete"}:
                operation = "trash"
        if action_kind in {"draft", "send"} and not raw["subject"] and not raw["draft_id"]:
            raise GoogleBridgeTaskError(f"Google step {step_index} requires subject for Gmail write operations.")
    else:
        if action_kind == "list":
            action_kind = "read"
        if action_kind == "read" and operation in {"create", "update", "delete"}:
            action_kind = operation
        if action_kind not in {"read", "create", "update", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports read, create, update, and delete actions for Calendar.")
        if action_kind == "read":
            if operation == "read":
                operation = "list"
            if operation not in {"list", "read"}:
                raise GoogleBridgeTaskError(f"Google step {step_index} currently supports list/read operations for Calendar reads.")
        elif action_kind == "create":
            if operation == "list":
                operation = "create"
            if operation != "create":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=create for Calendar create workflows.")
            if not raw["summary"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires summary for Calendar create workflows.")
            if not raw["start"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires start for Calendar create workflows.")
            if not raw["end"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires end for Calendar create workflows.")
        elif action_kind == "update":
            if operation == "list":
                operation = "update"
            if operation == "patch":
                operation = "update"
            if operation != "update":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=update for Calendar update workflows.")
            if not raw["event_id"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires event_id for Calendar update workflows.")
        elif action_kind == "delete":
            if operation == "list":
                operation = "delete"
            if operation != "delete":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=delete for Calendar delete workflows.")
            if not raw["event_id"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires event_id for Calendar delete workflows.")
    raw["action_kind"] = action_kind
    raw["operation"] = operation
    return raw


def _normalize_calendar_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise GoogleBridgeTaskError(
            "Calendar list queries require ISO 8601 timestamps. Use local time or an offset-aware timestamp."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(get_local_timezone_name()))
    local_zone = ZoneInfo(get_local_timezone_name())
    return parsed.astimezone(local_zone).isoformat()


def _normalize_calendar_timezone_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return get_local_timezone_name()
    try:
        return ZoneInfo(text).key
    except Exception as exc:
        raise GoogleBridgeTaskError(f"Calendar time_zone '{text}' is not a valid IANA timezone.") from exc


def _normalize_calendar_event_timestamp(value: object, time_zone_name: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    zone_name = _normalize_calendar_timezone_name(time_zone_name)
    zone = ZoneInfo(zone_name)
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise GoogleBridgeTaskError(
            "Calendar write timestamps require ISO 8601 local time or offset-aware timestamps."
        ) from exc
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return {"date": text}
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    parsed = parsed.astimezone(zone)
    return {"dateTime": parsed.isoformat(), "timeZone": zone.key}


def _normalize_calendar_send_updates(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"all", "none"}:
        return lowered
    if lowered == "externalonly":
        return "externalOnly"
    raise GoogleBridgeTaskError("Calendar send_updates must be all, externalOnly, or none.")


def _build_calendar_write_fields(normalized: dict[str, object], time_zone_name: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    summary = str(normalized.get("summary") or "").strip()
    if summary:
        payload["summary"] = summary
    description = str(normalized.get("description") or "").strip()
    if description:
        payload["description"] = description
    location = str(normalized.get("location") or "").strip()
    if location:
        payload["location"] = location
    start = _normalize_calendar_event_timestamp(normalized.get("start"), time_zone_name)
    if start:
        payload["start"] = start
    end = _normalize_calendar_event_timestamp(normalized.get("end"), time_zone_name)
    if end:
        payload["end"] = end
    attendees = _normalize_string_list(normalized.get("attendees"))
    if attendees:
        payload["attendees"] = attendees
    return payload


def _normalize_string_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    if isinstance(value, Iterable):
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _summarize_gmail_list(data: dict, *, unread_only: bool = False) -> str:
    messages = list(data.get("messages") or [])
    if not messages:
        return "No unread Gmail messages were returned." if unread_only else "No Gmail messages were returned."
    previews: list[str] = []
    for item in messages[:5]:
        message_id = str(item.get("id") or "").strip()
        subject = str(item.get("subject") or "").strip()
        sender = str(item.get("from") or "").strip()
        date = str(item.get("date") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        parts = []
        if message_id:
            parts.append(message_id)
        if subject:
            parts.append(f"subject={subject}")
        if sender:
            parts.append(f"from={sender}")
        if date:
            parts.append(f"date={date}")
        if snippet:
            parts.append(f"snippet={snippet[:80]}")
        if parts:
            previews.append(" | ".join(parts))
    count_label = "unread Gmail messages" if unread_only else "Gmail messages"
    return (
        f"Returned {len(messages)} {count_label}. Messages: {'; '.join(previews)}. "
        "Use google_bridge with operation=read and a message_id to fetch subject, sender, snippet, and metadata for each message."
    )


def _summarize_gmail_message(data: dict) -> str:
    headers = {str(header.get("name") or "").lower(): str(header.get("value") or "").strip() for header in data.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    snippet = str(data.get("snippet") or "").strip()
    parts = ["Gmail message retrieved."]
    if subject:
        parts.append(f"Subject: {subject}")
    if sender:
        parts.append(f"From: {sender}")
    if snippet:
        parts.append(f"Snippet: {snippet}")
    return " ".join(parts)


def _summarize_gmail_draft(data: dict) -> str:
    draft = dict(data.get("draft") or data)
    message = dict(draft.get("message") or {})
    draft_id = str(draft.get("id") or draft.get("draftId") or "").strip()
    message_id = str(message.get("id") or draft.get("messageId") or "").strip()
    thread_id = str(message.get("threadId") or draft.get("threadId") or "").strip()
    headers = {str(header.get("name") or "").lower(): str(header.get("value") or "").strip() for header in message.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "")
    to = headers.get("to", "")
    parts = ["Gmail draft created."]
    if draft_id:
        parts.append(f"Draft ID: {draft_id}")
    if message_id:
        parts.append(f"Message ID: {message_id}")
    if thread_id:
        parts.append(f"Thread ID: {thread_id}")
    if subject:
        parts.append(f"Subject: {subject}")
    if to:
        parts.append(f"To: {to}")
    return " ".join(parts)


def _summarize_gmail_send(data: dict) -> str:
    message_id = str(data.get("id") or data.get("messageId") or "").strip()
    thread_id = str(data.get("threadId") or "").strip()
    parts = ["Gmail message sent."]
    if message_id:
        parts.append(f"Message ID: {message_id}")
    if thread_id:
        parts.append(f"Thread ID: {thread_id}")
    return " ".join(parts)


def _summarize_gmail_trash(data: dict, *, message_id: str) -> str:
    parts = ["Gmail message moved to trash."]
    if message_id:
        parts.append(f"Message ID: {message_id}")
    return " ".join(parts)


def _summarize_gmail_delete(data: dict, *, message_id: str) -> str:
    parts = ["Gmail message permanently deleted."]
    if message_id:
        parts.append(f"Message ID: {message_id}")
    return " ".join(parts)


def _summarize_gmail_bulk_trash(message_ids: list[str]) -> str:
    ids = ", ".join(item for item in message_ids if item)
    return f"Gmail messages moved to trash. Count: {len(message_ids)}. Message IDs: {ids}."


def _summarize_gmail_bulk_delete(message_ids: list[str]) -> str:
    ids = ", ".join(item for item in message_ids if item)
    return f"Gmail messages permanently deleted. Count: {len(message_ids)}. Message IDs: {ids}."


def _summarize_gmail_no_matches_for_cleanup(*, queries: list[str], accounts: list[dict[str, object]]) -> str:
    query_text = ", ".join(query for query in queries if query.strip())
    account_text = ", ".join(
        str(item.get("email") or item.get("google_subject") or "").strip()
        for item in accounts
        if str(item.get("email") or item.get("google_subject") or "").strip()
    )
    if not account_text:
        account_text = "unknown accounts"
    if not query_text:
        query_text = "no query"
    return f"No Gmail messages matched this cleanup query. Checked accounts: {account_text}. Queries: {query_text}."


def _gmail_or_clause_limit() -> int:
    try:
        return max(1, int(getattr(settings, "GMAIL_OR_CLAUSE_LIMIT", 10) or 10))
    except Exception:
        return 10


def _expand_gmail_query_clauses(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return [""]
    clauses: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_quotes = False
    index = 0
    while index < len(text):
        if not in_quotes and text.startswith(" OR ", index):
            if depth > 0:
                raise GoogleBridgeTaskError(
                    "Malformed Gmail query: OR inside parentheses is not supported. "
                    "Use OR only between complete top-level clauses."
                )
            clause = _normalize_gmail_query_clause("".join(buffer).strip())
            if not clause:
                raise GoogleBridgeTaskError("Malformed Gmail query: empty OR clause.")
            clauses.append(clause)
            buffer = []
            index += 4
            continue
        ch = text[index]
        if ch == '"' and (index == 0 or text[index - 1] != "\\"):
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    raise GoogleBridgeTaskError("Malformed Gmail query: unmatched closing parenthesis.")
                depth -= 1
        buffer.append(ch)
        index += 1
    if in_quotes:
        raise GoogleBridgeTaskError("Malformed Gmail query: unmatched quote.")
    if depth != 0:
        raise GoogleBridgeTaskError("Malformed Gmail query: unmatched opening parenthesis.")
    final_clause = _normalize_gmail_query_clause("".join(buffer).strip())
    if not final_clause:
        if clauses:
            raise GoogleBridgeTaskError("Malformed Gmail query: empty OR clause.")
        return [""]
    clauses.append(final_clause)
    clause_limit = _gmail_or_clause_limit()
    if len(clauses) > clause_limit:
        raise GoogleBridgeTaskError(
            f"Malformed Gmail query: OR clauses are limited to {clause_limit} top-level clauses."
        )
    return clauses


def _normalize_gmail_query_clause(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)\bfrom:\s*@\s*", "from:", text)
    text = re.sub(r"(?i)\bfrom:\s+", "from:", text)
    text = re.sub(r"(?i)\bsubject:\s+", "subject:", text)
    text = re.sub(r"(?i)\blabel_ids:\s+", "label_ids:", text)
    text = re.sub(r"(?i)\bin:\s+", "in:", text)
    return re.sub(r"\s+", " ", text).strip()


def _expand_gmail_delete_queries(query: str) -> list[str]:
    return _expand_gmail_query_clauses(query)


def _collect_gmail_messages_for_queries(
    client: GoogleBridgeClient,
    *,
    queries: list[str],
    label_ids: list[str],
    max_results_per_query: int | None = None,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    seen_message_ids: set[str] = set()
    for query in queries:
        page_token = ""
        collected_for_query = 0
        seen_page_tokens: set[str] = set()
        while True:
            if max_results_per_query is not None and collected_for_query >= max_results_per_query:
                break
            if page_token in seen_page_tokens:
                break
            seen_page_tokens.add(page_token)
            request_max_results = 100
            if max_results_per_query is not None:
                request_max_results = min(request_max_results, max(1, max_results_per_query - collected_for_query))
            page = client.list_gmail_messages(
                query=query,
                label_ids=label_ids,
                max_results=request_max_results,
                page_token=page_token,
            )
            for item in list(page.get("messages") or []):
                message = dict(item)
                message_id = str(message.get("id") or "").strip()
                if message_id and message_id in seen_message_ids:
                    continue
                if message_id:
                    seen_message_ids.add(message_id)
                matches.append(message)
                collected_for_query += 1
                if max_results_per_query is not None and collected_for_query >= max_results_per_query:
                    break
            page_token = str(page.get("nextPageToken") or "").strip()
            if not page_token:
                break
    return matches


def _gmail_message_sort_key(item: dict[str, object]) -> tuple[float, str, str]:
    date_text = str(item.get("date") or "").strip()
    parsed = None
    if date_text:
        try:
            parsed = parsedate_to_datetime(date_text)
        except Exception:
            parsed = None
    timestamp = parsed.timestamp() if parsed is not None else 0.0
    subject = str(item.get("subject") or "").strip()
    message_id = str(item.get("id") or "").strip()
    return (-timestamp, subject, message_id)


def _enrich_gmail_list_messages(client: GoogleBridgeClient, data: dict) -> dict:
    messages = [dict(item) for item in list(data.get("messages") or [])]
    if not messages:
        return data
    enriched_messages: list[dict[str, object]] = []
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            enriched_messages.append(message)
            continue
        try:
            message_data = client.get_gmail_message(message_id)
        except Exception:
            enriched_messages.append(message)
            continue
        headers = {str(header.get("name") or "").lower(): str(header.get("value") or "").strip() for header in message_data.get("payload", {}).get("headers", [])}
        enriched_messages.append(
            {
                **message,
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
                "snippet": str(message_data.get("snippet") or "").strip(),
            }
        )
    enriched = dict(data)
    enriched["messages"] = enriched_messages
    return enriched


def _summarize_calendar_list(data: dict) -> str:
    events = list(data.get("items") or [])
    total = len(events)
    if not events:
        return "No calendar events were returned."
    preview = ", ".join(str(item.get("summary") or item.get("id") or "").strip() for item in events[:5] if str(item.get("summary") or item.get("id") or "").strip())
    return f"Found {total} calendar events. Event summaries: {preview}."


def _calendar_event_sort_key(item: dict[str, object]) -> tuple[str, str, str]:
    start = dict(item.get("start") or {})
    start_value = str(start.get("dateTime") or start.get("date") or "").strip()
    calendar_id = str(item.get("calendar_id") or "").strip()
    summary = str(item.get("summary") or item.get("id") or "").strip()
    return start_value, calendar_id, summary


def _summarize_calendar_event(data: dict) -> str:
    summary = str(data.get("summary") or "").strip()
    start = data.get("start", {})
    end = data.get("end", {})
    start_text = str(start.get("dateTime") or start.get("date") or "").strip()
    end_text = str(end.get("dateTime") or end.get("date") or "").strip()
    parts = ["Calendar event retrieved."]
    if summary:
        parts.append(f"Summary: {summary}")
    if start_text:
        parts.append(f"Start: {start_text}")
    if end_text:
        parts.append(f"End: {end_text}")
    return " ".join(parts)


def _summarize_calendar_mutation(data: dict, *, action: str) -> str:
    summary = str(data.get("summary") or "").strip()
    event_id = str(data.get("id") or data.get("eventId") or "").strip()
    html_link = str(data.get("htmlLink") or "").strip()
    parts = [f"Calendar event {action}."]
    if summary:
        parts.append(f"Summary: {summary}")
    if event_id:
        parts.append(f"Event ID: {event_id}")
    if html_link:
        parts.append(f"Link: {html_link}")
    return " ".join(parts)


def _summarize_calendar_delete(event_id: str) -> str:
    parts = ["Calendar event deleted."]
    if event_id:
        parts.append(f"Event ID: {event_id}")
    return " ".join(parts)
