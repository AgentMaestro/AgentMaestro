from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo
from django.utils import timezone

from google_bridge.models import GoogleAccount
from google_bridge.services.client import GoogleBridgeClient


class GoogleBridgeTaskError(RuntimeError):
    pass


_DEFAULT_CALENDAR_TIMEZONE = ZoneInfo("America/New_York")


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
            if not message_id:
                raise GoogleBridgeTaskError("gmail read tasks require message_id.")
            data = client.get_gmail_message(message_id)
            summary_text = _summarize_gmail_message(data)
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
            if not message_id:
                query = str(normalized.get("query") or "").strip()
                if not query:
                    raise GoogleBridgeTaskError("gmail delete tasks require message_id or a unique Gmail query.")
                matches = list(
                    client.list_gmail_messages(
                        query=query,
                        label_ids=list(normalized.get("label_ids") or []),
                        max_results=2,
                    ).get("messages") or []
                )
                if len(matches) != 1:
                    raise GoogleBridgeTaskError(
                        "gmail delete tasks require message_id or a Gmail query that matches exactly one message."
                    )
                message_id = str(matches[0].get("id") or "").strip()
                if not message_id:
                    raise GoogleBridgeTaskError(
                        "gmail delete tasks could not resolve a message_id from the Gmail query."
                    )
            if operation == "trash":
                data = client.trash_gmail_message(message_id)
                summary_text = _summarize_gmail_trash(data, message_id=message_id)
            else:
                data = client.delete_gmail_message(message_id)
                summary_text = _summarize_gmail_delete(data, message_id=message_id)
        else:
            data = client.list_gmail_messages(
                query=str(normalized.get("query") or "").strip(),
                label_ids=list(normalized.get("label_ids") or []),
                max_results=int(normalized.get("max_results") or 10),
            )
            summary_text = _summarize_gmail_list(data)
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
            data = client.list_calendar_events(
                calendar_id=str(normalized.get("calendar_id") or "primary").strip() or "primary",
                time_min=time_min,
                time_max=time_max,
                max_results=int(normalized.get("max_results") or 10),
            )
            summary_text = _summarize_calendar_list(data)
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
    if account is None and resource_kind == "gmail" and operation in {"trash", "delete"}:
        if not str(normalized.get("email") or "").strip() and not str(normalized.get("google_subject") or "").strip():
            candidate_count = GoogleAccount.objects.filter(is_active=True)
            if workspace is not None:
                candidate_count = candidate_count.filter(workspace=workspace)
            if owner is not None:
                candidate_count = candidate_count.filter(owner=owner)
            if candidate_count.count() != 1:
                raise GoogleBridgeTaskError(
                    "Gmail delete tasks require a specific connected account. Use email or google_subject to target the account explicitly."
                )
    if normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation == "list":
        result, summary_text = _execute_merged_list_task(selected_accounts, normalized, resource_kind)
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation in {"read", "trash", "delete"}:
        raise GoogleBridgeTaskError(
            "Google bridge read/delete-by-id tasks require the specific account from the list result. "
            "Use the returned account_email or google_subject together with message_id or event_id."
        )
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
    total_count = 0
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
            total_count += int(data.get("resultSizeEstimate") or len(items) or 0)
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
            total_count += len(items)
            for item in items:
                merged_items.append(
                    {
                        **dict(item),
                        "account_email": connection.email,
                        "account_google_subject": connection.google_subject,
                    }
                )

    if resource_kind == "gmail":
        return (
            {"messages": merged_items, "resultSizeEstimate": total_count, "accounts": account_summaries},
            f"Found {total_count} Gmail messages across {len(accounts)} accounts.",
        )

    return (
        {"items": merged_items, "accounts": account_summaries},
        f"Found {len(merged_items)} calendar events across {len(accounts)} accounts.",
    )


def _normalize_google_step(step: dict | None, *, defaults: dict[str, object] | None = None, step_index: int = 1) -> dict[str, object]:
    base = dict(defaults or {})
    raw = dict(base)
    raw.update(dict(step or {}))
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
    raw["max_results"] = int(raw.get("max_results") or 10)
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
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "draft", "send", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports read, draft, send, and delete actions for Gmail.")
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
        parsed = parsed.replace(tzinfo=_DEFAULT_CALENDAR_TIMEZONE)
    return parsed.astimezone(_DEFAULT_CALENDAR_TIMEZONE).isoformat()


def _normalize_calendar_timezone_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return _DEFAULT_CALENDAR_TIMEZONE.key
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


def _summarize_gmail_list(data: dict) -> str:
    messages = list(data.get("messages") or [])
    total = int(data.get("resultSizeEstimate") or len(messages) or 0)
    if not messages:
        return "No Gmail messages were returned."
    preview_ids = ", ".join(str(item.get("id") or "").strip() for item in messages[:5] if str(item.get("id") or "").strip())
    return (
        f"Found {total} Gmail messages. Message IDs: {preview_ids}. "
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


def _summarize_calendar_list(data: dict) -> str:
    events = list(data.get("items") or [])
    total = len(events)
    if not events:
        return "No calendar events were returned."
    preview = ", ".join(str(item.get("summary") or item.get("id") or "").strip() for item in events[:5] if str(item.get("summary") or item.get("id") or "").strip())
    return f"Found {total} calendar events. Event summaries: {preview}."


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
