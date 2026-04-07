from __future__ import annotations

from base64 import urlsafe_b64decode
from email import policy
from email.parser import BytesParser
import json
import re
from html import unescape
from collections.abc import Iterable
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone
from logging_utils import get_app_logger

from core.services.timezones import get_local_timezone_name
from google_bridge.models import GoogleAccount
from google_bridge.services.client import GoogleBridgeClient
from google_bridge.services.query_language import QueryLanguageError
from google_bridge.services.query_planner import QueryLiteral, QueryPlannerError, plan_google_query, _render_clause


class GoogleBridgeTaskError(RuntimeError):
    pass


logger = get_app_logger(__name__)


_PROMPT_FIELD_ORDER = (
    "account_scope",
    "email",
    "google_subject",
    "query",
    "person_fields",
    "read_mask",
    "person",
    "update_person_fields",
    "resource_name",
    "message_id",
    "filter_id",
    "criteria",
    "action",
    "dry_run",
    "preview_max_results",
    "file_id",
    "document_id",
    "spreadsheet_id",
    "range",
    "export_mime_type",
    "page_size",
    "page_token",
    "sort_order",
    "request_sync_token",
    "sync_token",
    "sources",
    "include_read",
    "to",
    "cc",
    "bcc",
    "subject",
    "body",
    "thread_id",
    "draft_id",
    "delete_mode",
    "label_ids",
    "max_results",
    "calendar_id",
    "summary",
    "description",
    "location",
    "start",
    "end",
    "time_zone",
    "attendees",
    "send_updates",
    "time_min",
    "time_max",
    "event_id",
)
_PROMPT_VALUE_LIMIT = 240


def _compact_prompt_value(value: object) -> str:
    if value in (None, "", [], {}, ()):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            compact = _compact_prompt_value(item)
            if compact:
                parts.append(f"{key}={compact}")
        return ", ".join(parts)
    if isinstance(value, list):
        parts = [_compact_prompt_value(item) for item in value]
        return ", ".join(part for part in parts if part)
    return str(value).strip()


def _append_prompt_field(lines: list[str], label: str, value: object) -> None:
    compact = _compact_prompt_value(value)
    if not compact:
        return
    if len(compact) > _PROMPT_VALUE_LIMIT:
        compact = f"{compact[:_PROMPT_VALUE_LIMIT].rstrip()}..."
    lines.append(f"- {label}: {compact}")


def _build_google_step_prompt(step: dict[str, object], *, index: int, total: int) -> str:
    resource_kind = str(step.get("resource_kind") or "").strip()
    action_kind = str(step.get("action_kind") or "").strip()
    operation = str(step.get("operation") or "").strip()
    header = f"Step {index}/{total}: {resource_kind} {action_kind} ({operation})".strip()
    lines = [header]
    for field_name in _PROMPT_FIELD_ORDER:
        if field_name not in step:
            continue
        _append_prompt_field(lines, field_name, step.get(field_name))
    return "\n".join(lines)


def build_google_task_objective(payload: dict | None) -> tuple[str, str]:
    normalized = normalize_google_payload(payload)
    objective = f"Complete the Google bridge task for {normalized['resource_kind']} {normalized['operation']}."
    steps = list(normalized.get("steps") or [])
    lines = [
        objective,
        "Use the Google Bridge tool to complete the task.",
    ]
    if steps:
        lines.append(f"Planned steps: {len(steps)}")
        for index, step in enumerate(steps, start=1):
            lines.append(_build_google_step_prompt(step, index=index, total=len(steps)))
    else:
        lines.append("Task parameters:")
        for field_name in _PROMPT_FIELD_ORDER:
            if field_name in normalized:
                _append_prompt_field(lines, field_name, normalized.get(field_name))
    lines.append("Return a concise summary of the result.")
    prompt = "\n".join(lines)
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


def resolve_google_account(
    *,
    workspace=None,
    owner=None,
    account_id: str = "",
    email: str = "",
    google_subject: str = "",
) -> GoogleAccount | None:
    queryset = GoogleAccount.objects.filter(is_active=True)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    if account_id:
        account = queryset.filter(id=account_id).first()
        if account is not None:
            return account
    if email:
        account = queryset.filter(email__iexact=email).first()
        if account is not None:
            return account
    if google_subject:
        account = queryset.filter(google_subject=google_subject).first()
        if account is not None:
            return account
    primary_email = str(getattr(settings, "GOOGLE_PRIMARY_ACCOUNT", "") or "").strip().lower()
    accounts = list(queryset.order_by("-last_synced_at", "-updated_at", "email", "google_subject"))
    if not accounts:
        return None
    if primary_email:
        for account in accounts:
            if str(account.email or "").strip().lower() == primary_email:
                return account
    for account in accounts:
        metadata = dict(account.metadata or {})
        if bool(metadata.get("is_primary")):
            return account
    return accounts[0]


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
        queryset = queryset.filter(email__iexact=email)
    accounts = list(queryset.order_by("-last_synced_at", "-updated_at", "email", "google_subject"))
    primary_email = str(getattr(settings, "GOOGLE_PRIMARY_ACCOUNT", "") or "").strip().lower()
    if account_scope != "all":
        account = resolve_google_account(
            workspace=workspace,
            owner=owner,
            google_subject=google_subject,
            email=email,
        )
        return [account] if account is not None else []
    if not accounts:
        return []
    primary_accounts: list[GoogleAccount] = []
    other_accounts: list[GoogleAccount] = []
    metadata_primary_accounts: list[GoogleAccount] = []
    for account in accounts:
        metadata = dict(account.metadata or {})
        if primary_email and str(account.email or "").strip().lower() == primary_email:
            primary_accounts.append(account)
        elif bool(metadata.get("is_primary")):
            metadata_primary_accounts.append(account)
        else:
            other_accounts.append(account)
    ordered_accounts = primary_accounts + metadata_primary_accounts + other_accounts
    return ordered_accounts


def set_primary_google_account(*, workspace=None, owner=None, account_id: str = "", email: str = "", google_subject: str = "") -> GoogleAccount | None:
    account = resolve_google_account(
        workspace=workspace,
        owner=owner,
        account_id=account_id,
        email=email,
        google_subject=google_subject,
    )
    if account is None:
        return None
    queryset = GoogleAccount.objects.filter(is_active=True)
    if workspace is not None:
        queryset = queryset.filter(workspace=workspace)
    if owner is not None:
        queryset = queryset.filter(owner=owner)
    current_timestamp = timezone.now().isoformat()
    for item in queryset:
        metadata = dict(item.metadata or {})
        changed = False
        if item.pk == account.pk:
            if not bool(metadata.get("is_primary")):
                metadata["is_primary"] = True
                changed = True
            metadata["primary_set_at"] = current_timestamp
            changed = True
        elif metadata.pop("is_primary", None) is not None:
            changed = True
        if changed:
            item.metadata = metadata
            item.save(update_fields=["metadata", "updated_at"])
    return account


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
                include_body = _normalize_bool(normalized.get("include_body"))
                include_html = _normalize_bool(normalized.get("include_html"))
                include_raw = _normalize_bool(normalized.get("include_raw"))
                include_attachments = _normalize_bool(normalized.get("include_attachments"))
                message_format = _normalize_gmail_message_format(
                    normalized,
                    include_body=include_body,
                    include_html=include_html,
                    include_raw=include_raw,
                    include_attachments=include_attachments,
                )
                data = client.get_gmail_message(message_id, format=message_format)
                if message_format == "full":
                    data = _expand_gmail_message_parts(
                        client,
                        data,
                        message_id=message_id,
                        include_body=include_body or message_format == "full",
                        include_html=include_html or message_format == "full",
                        include_attachments=include_attachments,
                    )
                elif message_format == "raw":
                    data = _expand_gmail_raw_message(
                        data,
                        include_body=include_body,
                        include_html=include_html,
                        include_attachments=include_attachments,
                    )
                data["message_format"] = message_format
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
                queries = _google_query_clauses(normalized)
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
                    data = {
                        "deleted_message_ids": deleted_ids,
                        "matched_queries": queries,
                        "query_plan": normalized.get("_google_query_plan") or {},
                    }
                    summary_text = (
                        _summarize_gmail_bulk_trash(deleted_ids)
                        if operation == "trash"
                        else _summarize_gmail_bulk_delete(deleted_ids)
                    )
                else:
                    data = {
                        "deleted_message_ids": [],
                        "matched_queries": queries,
                        "query_plan": normalized.get("_google_query_plan") or {},
                    }
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
    elif resource_kind == "gmail_settings":
        filter_id = str(normalized.get("filter_id") or "").strip()
        criteria = dict(normalized.get("criteria") or {})
        action_payload = dict(normalized.get("action") or {})
        query_clauses = list(normalized.get("_gmail_filter_query_clauses") or [])
        if not query_clauses and str(criteria.get("query") or "").strip():
            query_clauses = [str(criteria.get("query") or "").strip()]
        if operation in {"list", "read"}:
            if filter_id:
                data = client.get_gmail_filter(filter_id)
                summary_text = _summarize_gmail_filter(data)
            else:
                data = client.list_gmail_filters()
                summary_text = _summarize_gmail_filter_list(data)
        elif operation == "create":
            if bool(normalized.get("dry_run")):
                data = _build_gmail_filter_preview(
                    client,
                    normalized=normalized,
                    query_clauses=query_clauses,
                )
                summary_text = _summarize_gmail_filter_preview(data, action="would create")
            else:
                created_filters: list[dict[str, object]] = []
                candidate_criteria_list = _gmail_filter_candidate_criteria(criteria, query_clauses)
                for candidate_criteria in candidate_criteria_list:
                    created_filters.append(
                        client.create_gmail_filter(criteria=candidate_criteria, action=action_payload)
                    )
                if len(created_filters) == 1:
                    data = created_filters[0]
                    summary_text = _summarize_gmail_filter_mutation(data, action="created")
                else:
                    data = {"filters": created_filters, "count": len(created_filters)}
                    summary_text = _summarize_gmail_filter_batch_mutation(data, action="created")
        elif operation == "update":
            if not filter_id:
                raise GoogleBridgeTaskError("Gmail filter update tasks require filter_id.")
            existing = client.get_gmail_filter(filter_id)
            merged_criteria = dict(existing.get("criteria") or {})
            merged_action = dict(existing.get("action") or {})
            merged_criteria.update(criteria)
            merged_action.update(action_payload)
            if bool(normalized.get("dry_run")):
                preview_query_clauses = list(normalized.get("_gmail_filter_query_clauses") or [])
                if not preview_query_clauses and str(merged_criteria.get("query") or "").strip():
                    preview_query_clauses = [str(merged_criteria.get("query") or "").strip()]
                data = _build_gmail_filter_preview(
                    client,
                    normalized=normalized,
                    query_clauses=preview_query_clauses,
                )
                summary_text = _summarize_gmail_filter_preview(data, action="would update")
            else:
                client.delete_gmail_filter(filter_id)
                created_filters = []
                candidate_criteria_list = _gmail_filter_candidate_criteria(merged_criteria, list(normalized.get("_gmail_filter_query_clauses") or []))
                for candidate_criteria in candidate_criteria_list:
                    created_filters.append(
                        client.create_gmail_filter(criteria=candidate_criteria, action=merged_action)
                    )
                if len(created_filters) == 1:
                    data = created_filters[0]
                    summary_text = _summarize_gmail_filter_mutation(data, action="updated")
                else:
                    data = {"filters": created_filters, "count": len(created_filters)}
                    summary_text = _summarize_gmail_filter_batch_mutation(data, action="updated")
        elif operation == "delete":
            if not filter_id:
                raise GoogleBridgeTaskError("Gmail filter delete tasks require filter_id.")
            data = client.delete_gmail_filter(filter_id)
            summary_text = _summarize_gmail_filter_delete(filter_id)
        else:
            raise GoogleBridgeTaskError(f"Unsupported Google gmail_settings operation '{operation}'.")
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
            queries = _google_query_clauses(normalized)
            data, summary_text = _execute_calendar_list_for_account(
                client=client,
                normalized=normalized,
                queries=queries,
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
    elif resource_kind == "drive":
        if operation in {"list", "read"}:
            if operation == "list":
                data, summary_text = _execute_drive_list_for_account(client=client, normalized=normalized)
            else:
                data, summary_text = _execute_drive_read_for_account(client=client, normalized=normalized)
        elif operation == "export":
            data, summary_text = _execute_drive_export_for_account(client=client, normalized=normalized)
        else:
            raise GoogleBridgeTaskError(f"Unsupported Google drive operation '{operation}'.")
    elif resource_kind == "people":
        if operation == "list":
            data, summary_text = _execute_people_list_for_account(client=client, normalized=normalized)
        elif operation == "search":
            data, summary_text = _execute_people_search_for_account(client=client, normalized=normalized)
        elif operation == "read":
            data, summary_text = _execute_people_read_for_account(client=client, normalized=normalized)
        elif operation == "create":
            data, summary_text = _execute_people_create_for_account(client=client, normalized=normalized)
        elif operation == "update":
            data, summary_text = _execute_people_update_for_account(client=client, normalized=normalized)
        elif operation == "delete":
            data, summary_text = _execute_people_delete_for_account(client=client, normalized=normalized)
        else:
            raise GoogleBridgeTaskError(f"Unsupported Google people operation '{operation}'.")
    elif resource_kind == "docs":
        if operation == "export":
            data, summary_text = _execute_docs_export_for_account(client=client, normalized=normalized)
        elif operation in {"list", "read"}:
            data, summary_text = _execute_docs_read_for_account(client=client, normalized=normalized)
        else:
            raise GoogleBridgeTaskError(f"Unsupported Google docs operation '{operation}'.")
    elif resource_kind == "sheets":
        if operation == "export":
            data, summary_text = _execute_sheets_export_for_account(client=client, normalized=normalized)
        elif operation in {"list", "read"}:
            data, summary_text = _execute_sheets_read_for_account(client=client, normalized=normalized)
        else:
            raise GoogleBridgeTaskError(f"Unsupported Google sheets operation '{operation}'.")
    else:
        raise GoogleBridgeTaskError(f"Unsupported Google resource kind '{resource_kind}'.")
    return data, summary_text


def _execute_calendar_list_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
    queries: list[str],
    time_min: str,
    time_max: str,
) -> tuple[dict[str, object], str]:
    max_results = int(normalized.get("max_results") or 20)
    calendar_id = str(normalized.get("calendar_id") or "").strip()
    explicit_calendar_id = bool(normalized.get("_calendar_id_explicit"))
    queries = [str(query or "").strip() for query in list(queries or [])]
    if not queries:
        queries = [""]
    query_plan = normalized.get("_google_query_plan") or {}
    if calendar_id and calendar_id != "all" and (explicit_calendar_id or calendar_id != "primary"):
        items: list[dict[str, object]] = []
        seen_event_keys: set[tuple[str, str]] = set()
        for query in queries:
            page = client.list_calendar_events(
                calendar_id=calendar_id,
                q=query,
                time_min=time_min,
                time_max=time_max,
                max_results=max_results,
            )
            for item in list(page.get("items") or []):
                event = dict(item)
                event_id = str(event.get("id") or "").strip()
                event_key = (calendar_id, event_id)
                if event_key in seen_event_keys:
                    continue
                seen_event_keys.add(event_key)
                items.append(
                    {
                        **event,
                        "calendar_id": calendar_id,
                    }
                )
        data = {
            "kind": "calendar#events",
            "items": items[:max_results],
            "calendars": [
                {
                    "id": calendar_id,
                    "summary": calendar_id,
                    "selected": True,
                }
            ],
            "resultSizeEstimate": len(items[:max_results]),
            "query_plan": query_plan,
            "matched_queries": [query for query in queries if query],
        }
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
    seen_event_keys: set[tuple[str, str]] = set()
    calendar_summaries: list[dict[str, object]] = []
    for calendar in calendars:
        calendar_id_value = str(calendar.get("id") or "").strip()
        if not calendar_id_value:
            continue
        calendar_summary = str(calendar.get("summary") or calendar_id_value).strip() or calendar_id_value
        calendar_summaries.append(
            {
                "id": calendar_id_value,
                "summary": calendar_summary,
                "primary": bool(calendar.get("primary")),
                "selected": bool(calendar.get("selected")),
            }
        )
        for query in queries:
            events = client.list_calendar_events(
                calendar_id=calendar_id_value,
                q=query,
                time_min=time_min,
                time_max=time_max,
                max_results=max_results,
            )
            for item in list(events.get("items") or []):
                event = dict(item)
                event_id = str(event.get("id") or "").strip()
                event_key = (calendar_id_value, event_id)
                if event_key in seen_event_keys:
                    continue
                seen_event_keys.add(event_key)
                merged_items.append(
                    {
                        **event,
                        "calendar_id": calendar_id_value,
                        "calendar_summary": calendar_summary,
                    }
                )

    merged_items.sort(key=_calendar_event_sort_key)
    merged_items = merged_items[:max_results]
    data = {
        "kind": "calendar#events",
        "items": merged_items,
        "calendars": calendar_summaries,
        "resultSizeEstimate": len(merged_items),
        "query_plan": query_plan,
        "matched_queries": [query for query in queries if query],
    }
    return data, _summarize_calendar_list(data)


def _execute_people_list_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    person_fields = str(normalized.get("person_fields") or "").strip()
    if not person_fields:
        raise GoogleBridgeTaskError("People list tasks require person_fields.")
    data = client.list_people_connections(
        person_fields=person_fields,
        page_size=int(normalized.get("page_size") or 100),
        page_token=str(normalized.get("page_token") or ""),
        sort_order=str(normalized.get("sort_order") or ""),
        request_sync_token=_normalize_bool(normalized.get("request_sync_token")),
        sync_token=str(normalized.get("sync_token") or ""),
        sources=_normalize_string_list(normalized.get("sources")),
    )
    normalized_data = {
        "connections": list(data.get("connections") or []),
        "nextPageToken": data.get("nextPageToken") or "",
        "nextSyncToken": data.get("nextSyncToken") or "",
        "totalPeople": int(data.get("totalPeople") or 0),
        "totalItems": int(data.get("totalItems") or 0),
        "person_fields": person_fields,
        "page_size": int(normalized.get("page_size") or 100),
        "page_token": str(normalized.get("page_token") or ""),
        "sort_order": str(normalized.get("sort_order") or ""),
        "request_sync_token": bool(_normalize_bool(normalized.get("request_sync_token"))),
        "sync_token": str(normalized.get("sync_token") or ""),
        "sources": _normalize_string_list(normalized.get("sources")),
    }
    return normalized_data, _summarize_people_connections(normalized_data)


def _execute_people_search_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    query = str(normalized.get("query") or "").strip()
    if not query:
        raise GoogleBridgeTaskError("People search tasks require query.")
    read_mask = str(normalized.get("read_mask") or normalized.get("person_fields") or "").strip()
    if not read_mask:
        raise GoogleBridgeTaskError("People search tasks require read_mask or person_fields.")
    data = client.search_people_contacts(
        query=query,
        read_mask=read_mask,
        page_size=int(normalized.get("page_size") or 10),
        page_token=str(normalized.get("page_token") or ""),
        sources=_normalize_string_list(normalized.get("sources")),
    )
    normalized_data = {
        "results": list(data.get("results") or []),
        "query": query,
        "read_mask": read_mask,
        "page_size": int(normalized.get("page_size") or 10),
        "page_token": str(normalized.get("page_token") or ""),
        "sources": _normalize_string_list(normalized.get("sources")),
    }
    return normalized_data, _summarize_people_search(normalized_data)


def _execute_people_read_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    resource_name = str(normalized.get("resource_name") or "").strip()
    if not resource_name:
        raise GoogleBridgeTaskError("People read tasks require resource_name.")
    person_fields = str(normalized.get("person_fields") or "").strip()
    if not person_fields:
        raise GoogleBridgeTaskError("People read tasks require person_fields.")
    data = client.get_people(
        resource_name,
        person_fields=person_fields,
        sources=_normalize_string_list(normalized.get("sources")),
    )
    normalized_data = dict(data)
    normalized_data["resource_name"] = resource_name
    normalized_data["person_fields"] = person_fields
    normalized_data["sources"] = _normalize_string_list(normalized.get("sources"))
    return normalized_data, _summarize_people_person(normalized_data)


def _execute_people_create_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    person = _normalize_people_contact_payload(normalized.get("person"))
    if not person:
        raise GoogleBridgeTaskError("People create tasks require person.")
    person_fields = _normalize_people_field_mask(normalized.get("person_fields")) or "names,emailAddresses,phoneNumbers,metadata"
    data = client.create_people_contact(
        person=person,
        person_fields=person_fields,
    )
    normalized_data = dict(data)
    normalized_data["person"] = person
    normalized_data["person_fields"] = person_fields
    return normalized_data, _summarize_people_person_action(normalized_data, action="created")


def _execute_people_update_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    resource_name = str(normalized.get("resource_name") or "").strip()
    if not resource_name:
        raise GoogleBridgeTaskError("People update tasks require resource_name.")
    person = _normalize_people_contact_payload(normalized.get("person"))
    if not person:
        raise GoogleBridgeTaskError("People update tasks require person.")
    update_person_fields = _normalize_people_field_mask(normalized.get("update_person_fields"))
    if not update_person_fields:
        raise GoogleBridgeTaskError("People update tasks require update_person_fields.")
    if not _people_contact_has_sources(person):
        raise GoogleBridgeTaskError("People update tasks require person.metadata.sources.")
    if not _people_contact_has_etag(person):
        raise GoogleBridgeTaskError(
            "People update tasks require person.etag or person.metadata.sources[].etag. Read the contact first, then reuse the current etag before updating."
        )
    person_fields = _normalize_people_field_mask(normalized.get("person_fields")) or "names,emailAddresses,phoneNumbers,metadata"
    data = client.update_people_contact(
        resource_name,
        person=person,
        person_fields=person_fields,
        update_person_fields=update_person_fields,
    )
    normalized_data = dict(data)
    normalized_data["resource_name"] = resource_name
    normalized_data["person"] = person
    normalized_data["person_fields"] = person_fields
    normalized_data["update_person_fields"] = update_person_fields
    return normalized_data, _summarize_people_person_action(normalized_data, action="updated")


def _execute_people_delete_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    resource_name = str(normalized.get("resource_name") or "").strip()
    if not resource_name:
        raise GoogleBridgeTaskError("People delete tasks require resource_name.")
    client.delete_people_contact(resource_name)
    normalized_data = {
        "resource_name": resource_name,
        "deleted": True,
    }
    return normalized_data, f"Google contact deleted. Resource: {resource_name}"


def _execute_drive_list_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    query = str(normalized.get("query") or "").strip()
    if query:
        query_plan = _plan_google_query(query, resource_kind="drive", action_kind="read", operation="list")
        query = str(query_plan.normalized_query or query).strip()
        logger.info(
            "Drive list query rendered original=%r normalized=%r rendered=%r max_results=%s",
            query_plan.original_query,
            query_plan.normalized_query,
            query,
            int(normalized.get("max_results") or 20),
        )
    page_size = int(normalized.get("max_results") or 20)
    data = client.list_drive_files(q=query, page_size=page_size)
    files = list(data.get("files") or [])
    data = {
        "kind": "drive#files",
        "files": files[:page_size],
        "nextPageToken": data.get("nextPageToken") or "",
        "query": query,
    }
    return data, _summarize_drive_list(data, query=query)


def _execute_drive_read_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    if not file_id:
        return _execute_drive_list_for_account(client=client, normalized=normalized)
    data = client.get_drive_file(
        file_id,
        fields="id,name,mimeType,modifiedTime,createdTime,webViewLink,webContentLink,size,owners,emailAddress",
    )
    return data, _summarize_drive_file(data)


def _execute_drive_export_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    export_mime_type = str(normalized.get("export_mime_type") or "").strip()
    if not file_id:
        raise GoogleBridgeTaskError("Drive export tasks require file_id.")
    if not export_mime_type:
        raise GoogleBridgeTaskError("Drive export tasks require export_mime_type.")
    content = client.export_drive_file(file_id, export_mime_type)
    return (
        {
            "file_id": file_id,
            "export_mime_type": export_mime_type,
            "content_text": _decode_text_bytes(content, export_mime_type),
            "content_bytes_base64": _encode_bytes_base64(content),
            "content_length": len(content),
        },
        _summarize_drive_file({"id": file_id, "mimeType": export_mime_type, "name": str(normalized.get("name") or "")}),
    )


def _execute_docs_read_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    if not file_id:
        raise GoogleBridgeTaskError("Docs read tasks require file_id.")
    data = client.get_document(file_id)
    return data, _summarize_docs_document(data)


def _execute_docs_export_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    export_mime_type = str(normalized.get("export_mime_type") or "").strip()
    if not file_id:
        raise GoogleBridgeTaskError("Docs export tasks require file_id.")
    if not export_mime_type:
        raise GoogleBridgeTaskError("Docs export tasks require export_mime_type.")
    content = client.export_drive_file(file_id, export_mime_type)
    return (
        {
            "file_id": file_id,
            "export_mime_type": export_mime_type,
            "content_text": _decode_text_bytes(content, export_mime_type),
            "content_bytes_base64": _encode_bytes_base64(content),
            "content_length": len(content),
        },
        _summarize_docs_export(file_id, export_mime_type, content),
    )


def _execute_sheets_read_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    range_name = str(normalized.get("range") or "").strip()
    if not file_id:
        raise GoogleBridgeTaskError("Sheets read tasks require file_id.")
    data = client.get_sheet_values(file_id, range_name=range_name)
    return data, _summarize_sheet_values(data, spreadsheet_id=file_id, range_name=range_name)


def _execute_sheets_export_for_account(
    *,
    client: GoogleBridgeClient,
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    file_id = _resolve_google_file_id(normalized)
    export_mime_type = str(normalized.get("export_mime_type") or "").strip()
    if not file_id:
        raise GoogleBridgeTaskError("Sheets export tasks require file_id.")
    if not export_mime_type:
        raise GoogleBridgeTaskError("Sheets export tasks require export_mime_type.")
    content = client.export_drive_file(file_id, export_mime_type)
    return (
        {
            "file_id": file_id,
            "export_mime_type": export_mime_type,
            "content_text": _decode_text_bytes(content, export_mime_type),
            "content_bytes_base64": _encode_bytes_base64(content),
            "content_length": len(content),
        },
        _summarize_sheet_export(file_id, export_mime_type, content),
    )


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
    if (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "people"
        and operation in {"create", "update", "delete"}
    ):
        raise GoogleBridgeTaskError(
            "People write tasks require a specific connected account. Use email or google_subject to target the account explicitly."
        )
    if (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "drive"
        and operation in {"list", "read"}
        and not str(normalized.get("file_id") or "").strip()
    ):
        result, summary_text = _execute_merged_list_task(selected_accounts, normalized, resource_kind)
    elif (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "gmail_settings"
        and operation in {"list", "read"}
        and not str(normalized.get("filter_id") or "").strip()
    ):
        result, summary_text = _execute_merged_gmail_settings_list_task(selected_accounts, normalized)
    elif (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "people"
        and operation in {"list", "search"}
    ):
        result, summary_text = _execute_merged_people_task(selected_accounts, normalized, operation)
    elif (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "drive"
        and operation in {"read", "export"}
        and str(normalized.get("file_id") or "").strip()
    ):
        raise GoogleBridgeTaskError(
            "Drive file read/export tasks require a specific connected account. Use email or google_subject to target the account explicitly."
        )
    elif (
        normalized.get("account_scope") == "all"
        and len(selected_accounts) > 1
        and resource_kind == "people"
        and operation == "read"
        and str(normalized.get("resource_name") or "").strip()
    ):
        raise GoogleBridgeTaskError(
            "People read-by-resource-name tasks require a specific connected account. Use email or google_subject to target the account explicitly."
        )
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and resource_kind in {"docs", "sheets"}:
        raise GoogleBridgeTaskError(
            f"{resource_kind.title()} read/export tasks require a specific connected account. Use email or google_subject to target the account explicitly."
        )
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation in {"list", "read"} and not str(normalized.get("message_id") or "").strip():
        result, summary_text = _execute_merged_list_task(selected_accounts, normalized, resource_kind)
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and operation == "read":
        raise GoogleBridgeTaskError(
            "Google bridge read tasks require the specific account from the list result. "
            "Use the returned account_email or google_subject together with message_id or event_id."
        )
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and resource_kind == "gmail_settings" and str(normalized.get("filter_id") or "").strip():
        raise GoogleBridgeTaskError(
            "Gmail filter read-by-id tasks require a specific connected account. Use email or google_subject to target the account explicitly."
        )
    elif normalized.get("account_scope") == "all" and len(selected_accounts) > 1 and resource_kind == "gmail_settings" and operation in {"create", "update", "delete"}:
        raise GoogleBridgeTaskError(
            "Gmail filter write tasks require a specific connected account. Use email or google_subject to target the account explicitly."
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
        elif resource_kind == "drive":
            items = list(data.get("files") or [])
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

    if resource_kind == "drive":
        max_results = int(normalized.get("max_results") or 20)
        merged_items = merged_items[:max_results]
        return (
            {"files": merged_items, "nextPageToken": "", "accounts": account_summaries},
            f"Returned {len(merged_items)} Drive files across {len(accounts)} accounts.",
        )

    return (
        {"items": merged_items, "calendars": merged_calendars, "accounts": account_summaries},
        f"Found {len(merged_items)} calendar events across {len(accounts)} accounts and {len(merged_calendars)} calendars.",
    )


def _execute_merged_people_task(
    accounts: list[GoogleAccount],
    normalized: dict[str, object],
    operation: str,
) -> tuple[dict[str, object], str]:
    merged_people: list[dict[str, object]] = []
    account_summaries: list[dict[str, object]] = []
    seen_resource_names: set[str] = set()
    for connection in accounts:
        data, summary_text = _execute_google_task_for_account(connection, normalized, "people", operation)
        account_summaries.append(
            {
                "google_subject": connection.google_subject,
                "email": connection.email,
                "summary_text": summary_text,
            }
        )
        if operation == "search":
            items = list(data.get("results") or [])
            for item in items:
                person = _extract_people_person(item)
                resource_name = _people_resource_name(person)
                if resource_name and resource_name in seen_resource_names:
                    continue
                if resource_name:
                    seen_resource_names.add(resource_name)
                merged_people.append({"person": person})
        else:
            items = list(data.get("connections") or [])
            for item in items:
                person = dict(item or {})
                resource_name = _people_resource_name(person)
                if resource_name and resource_name in seen_resource_names:
                    continue
                if resource_name:
                    seen_resource_names.add(resource_name)
                merged_people.append(person)

    max_results = int(normalized.get("max_results") or 20)
    merged_people = merged_people[:max_results]
    if operation == "search":
        return (
            {"results": merged_people, "accounts": account_summaries, "query": str(normalized.get("query") or "").strip()},
            f"Returned {len(merged_people)} Google contacts search results across {len(accounts)} accounts.",
        )
    return (
        {
            "connections": merged_people,
            "accounts": account_summaries,
            "nextPageToken": "",
            "nextSyncToken": "",
            "totalPeople": len(merged_people),
            "totalItems": len(merged_people),
        },
        f"Returned {len(merged_people)} Google contacts across {len(accounts)} accounts.",
    )


def _execute_merged_gmail_settings_list_task(
    accounts: list[GoogleAccount],
    normalized: dict[str, object],
) -> tuple[dict[str, object], str]:
    merged_filters: list[dict[str, object]] = []
    account_summaries: list[dict[str, object]] = []
    for connection in accounts:
        data, summary_text = _execute_google_task_for_account(connection, normalized, "gmail_settings", "list")
        account_summaries.append(
            {
                "google_subject": connection.google_subject,
                "email": connection.email,
                "summary_text": summary_text,
            }
        )
        for item in list(data.get("filter") or data.get("filters") or []):
            merged_filters.append(
                {
                    **dict(item),
                    "account_email": connection.email,
                    "account_google_subject": connection.google_subject,
                }
            )
    return (
        {"filter": merged_filters, "accounts": account_summaries},
        f"Returned {len(merged_filters)} Gmail filters across {len(accounts)} accounts.",
    )


def _execute_merged_delete_task(
    accounts: list[GoogleAccount],
    normalized: dict[str, object],
    operation: str,
) -> tuple[dict[str, object], str]:
    account_summaries: list[dict[str, object]] = []
    deleted_message_ids: list[str] = []
    queries = _google_query_clauses(normalized)
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
            "query_plan": normalized.get("_google_query_plan") or {},
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
            "query_plan": normalized.get("_google_query_plan") or {},
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
    queries = _google_query_clauses(normalized)
    shared_corpus_plan = _gmail_shared_corpus_plan(normalized)
    if shared_corpus_plan is not None:
        base_query, clause_remainders = shared_corpus_plan
        raw_messages = _collect_gmail_messages_for_queries(
            client,
            queries=[base_query],
            label_ids=list(normalized.get("label_ids") or []),
            max_results_per_query=None,
        )
        data = {
            "messages": raw_messages,
            "resultSizeEstimate": len(raw_messages),
            "query_plan": normalized.get("_google_query_plan") or {},
            "execution_strategy": "shared_corpus",
            "shared_base_query": base_query,
        }
        data = _enrich_gmail_list_messages(client, data)
        enriched_messages = list(data.get("messages") or [])
        filtered_messages = [
            message
            for message in enriched_messages
            if _gmail_message_matches_any_clause(message, clause_remainders)
        ]
        filtered_messages.sort(key=_gmail_message_sort_key)
        filtered_messages = filtered_messages[:max_results]
        data = dict(data)
        data["messages"] = filtered_messages
        data["resultSizeEstimate"] = len(filtered_messages)
        return data

    raw_messages = _collect_gmail_messages_for_queries(
        client,
        queries=queries,
        label_ids=list(normalized.get("label_ids") or []),
        max_results_per_query=max_results,
    )
    data = {
        "messages": raw_messages,
        "resultSizeEstimate": len(raw_messages),
        "query_plan": normalized.get("_google_query_plan") or {},
        "execution_strategy": "query_fanout",
    }
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
    if resource_kind not in {"gmail", "gmail_settings", "calendar", "drive", "docs", "sheets", "people"}:
        raise GoogleBridgeTaskError(
            f"Google step {step_index} requires resource_kind=gmail, gmail_settings, calendar, drive, docs, sheets, or people."
        )
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
    raw["filter_id"] = str(raw.get("filter_id") or "").strip()
    raw["criteria"] = _normalize_gmail_filter_payload(raw.get("criteria"))
    raw["action"] = _normalize_gmail_filter_payload(raw.get("action"))
    raw["dry_run"] = _normalize_bool(raw.get("dry_run"))
    raw["preview_max_results"] = int(raw.get("preview_max_results") or 5)
    raw["file_id"] = str(
        raw.get("file_id")
        or raw.get("document_id")
        or raw.get("spreadsheet_id")
        or ""
    ).strip()
    raw["document_id"] = str(raw.get("document_id") or raw["file_id"] or "").strip()
    raw["spreadsheet_id"] = str(raw.get("spreadsheet_id") or raw["file_id"] or "").strip()
    raw["range"] = str(raw.get("range") or "").strip()
    raw["export_mime_type"] = str(raw.get("export_mime_type") or "").strip()
    if resource_kind == "gmail":
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "draft", "send", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports read, draft, send, and delete actions for Gmail.")
        if operation == "list" and not raw["_gmail_list_include_read"] and not str(raw.get("query") or "").strip() and not list(raw.get("label_ids") or []):
            raw["query"] = "is:unread"
            raw["_gmail_list_default_unread"] = True
        if action_kind in {"read", "delete"} and str(raw.get("query") or "").strip():
            query_plan = _plan_google_query(
                str(raw.get("query") or "").strip(),
                resource_kind=resource_kind,
                action_kind=action_kind,
                operation=operation,
            )
            raw["_google_query_plan"] = query_plan.to_dict()
            raw["_google_query_clauses"] = list(query_plan.query_strings)
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
    elif resource_kind == "gmail_settings":
        if str(raw.get("query") or "").strip():
            raise GoogleBridgeTaskError(
                f"Google step {step_index} does not support top-level query strings for Gmail settings filters. Use criteria.query instead."
            )
        criteria_query = str(dict(raw.get("criteria") or {}).get("query") or "").strip()
        if criteria_query:
            query_plan = _plan_google_query(
                criteria_query,
                resource_kind="gmail",
                action_kind="read",
                operation="list",
            )
            raw["_gmail_filter_query_plan"] = query_plan.to_dict()
            raw["_gmail_filter_query_clauses"] = list(query_plan.query_strings)
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "create", "update", "delete"}:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports read, create, update, and delete actions for Gmail settings filters."
            )
        if action_kind == "read":
            if operation == "read":
                operation = "list"
            if operation not in {"list", "read"}:
                raise GoogleBridgeTaskError(f"Google step {step_index} currently supports list/read operations for Gmail settings filters.")
        elif action_kind == "create":
            if operation == "list":
                operation = "create"
            if operation != "create":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=create for Gmail filter create workflows.")
            if not raw["criteria"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires criteria for Gmail filter create workflows.")
            if not raw["action"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires action for Gmail filter create workflows.")
        elif action_kind == "update":
            if operation in {"list", "read"}:
                operation = "update"
            if operation == "patch":
                operation = "update"
            if operation != "update":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=update for Gmail filter update workflows.")
            if not raw["filter_id"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires filter_id for Gmail filter update workflows.")
            if not raw["criteria"] and not raw["action"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires criteria or action for Gmail filter update workflows.")
        elif action_kind == "delete":
            if operation == "list":
                operation = "delete"
            if operation != "delete":
                raise GoogleBridgeTaskError(f"Google step {step_index} requires operation=delete for Gmail filter delete workflows.")
            if not raw["filter_id"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires filter_id for Gmail filter delete workflows.")
    elif resource_kind == "calendar":
        if action_kind == "list":
            action_kind = "read"
        if action_kind == "read" and operation in {"create", "update", "delete"}:
            action_kind = operation
        if action_kind not in {"read", "create", "update", "delete"}:
            raise GoogleBridgeTaskError(f"Google step {step_index} currently supports read, create, update, and delete actions for Calendar.")
        if action_kind == "read" and str(raw.get("query") or "").strip():
            query_plan = _plan_google_query(
                str(raw.get("query") or "").strip(),
                resource_kind=resource_kind,
                action_kind=action_kind,
                operation=operation,
            )
            raw["_google_query_plan"] = query_plan.to_dict()
            raw["_google_query_clauses"] = list(query_plan.query_strings)
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
    elif resource_kind == "people":
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "search", "create", "update", "delete"}:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports read, search, create, update, and delete actions for People contacts."
            )
        raw["person_fields"] = _normalize_people_field_mask(raw.get("person_fields") or raw.get("read_mask"))
        raw["read_mask"] = _normalize_people_field_mask(raw.get("read_mask") or raw.get("person_fields"))
        raw["resource_name"] = str(raw.get("resource_name") or "").strip()
        raw["page_token"] = str(raw.get("page_token") or "").strip()
        raw["sort_order"] = str(raw.get("sort_order") or "").strip()
        raw["sync_token"] = str(raw.get("sync_token") or "").strip()
        raw["sources"] = _normalize_string_list(raw.get("sources"))
        page_size_default = 10 if operation == "search" else 100
        if action_kind == "search" and operation == "list":
            operation = "search"
        if action_kind in {"create", "update", "delete"} and operation == "list":
            operation = action_kind
        if action_kind in {"create", "update", "delete"} and operation != action_kind:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} requires operation={action_kind} for People {action_kind} workflows."
            )
        raw["page_size"] = int(raw.get("page_size") or page_size_default)
        raw["person"] = _normalize_people_contact_payload(raw.get("person"))
        raw["update_person_fields"] = _normalize_people_field_mask(raw.get("update_person_fields"))
        if operation == "list":
            if raw["query"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} does not support query strings for People list workflows."
                )
            if not raw["person_fields"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires person_fields for People list workflows.")
        elif operation == "search":
            if not raw["query"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires query for People search workflows.")
            if not raw["read_mask"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires read_mask or person_fields for People search workflows."
                )
        elif operation == "read":
            if not raw["resource_name"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires resource_name for People read workflows.")
            if not raw["person_fields"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires person_fields for People read workflows.")
        elif operation == "create":
            if action_kind != "create":
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires action_kind=create for People create workflows."
                )
            if not raw["person"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires person for People create workflows.")
            if not raw["person_fields"]:
                raw["person_fields"] = "names,emailAddresses,phoneNumbers,metadata"
            raw["read_mask"] = raw["person_fields"]
        elif operation == "update":
            if action_kind != "update":
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires action_kind=update for People update workflows."
                )
            if not raw["resource_name"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires resource_name for People update workflows.")
            if not raw["person"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires person for People update workflows.")
            if not raw["update_person_fields"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires update_person_fields for People update workflows."
                )
            if not _people_contact_has_sources(raw["person"]):
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires person.metadata.sources for People update workflows."
                )
            if not _people_contact_has_etag(raw["person"]):
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires person.etag or person.metadata.sources[].etag for People update workflows. Read the contact first, then reuse the current etag before updating."
                )
            if not raw["person_fields"]:
                raw["person_fields"] = "names,emailAddresses,phoneNumbers,metadata"
            raw["read_mask"] = raw["person_fields"]
        elif operation == "delete":
            if action_kind != "delete":
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires action_kind=delete for People delete workflows."
                )
            if not raw["resource_name"]:
                raise GoogleBridgeTaskError(f"Google step {step_index} requires resource_name for People delete workflows.")
        else:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports list, search, read, create, update, and delete operations for People contacts."
            )
    else:
        if action_kind == "list":
            action_kind = "read"
        if action_kind not in {"read", "export"}:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports read and export actions for Drive, Docs, and Sheets."
            )
        if resource_kind in {"docs", "sheets"} and str(raw.get("query") or "").strip():
            raise GoogleBridgeTaskError(
                f"Google step {step_index} does not support query strings for {resource_kind} reads."
            )
        if resource_kind == "drive" and action_kind == "read" and operation not in {"list", "read"}:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports list/read operations for Drive reads."
            )
        if resource_kind in {"docs", "sheets"} and action_kind == "read" and operation not in {"list", "read"}:
            raise GoogleBridgeTaskError(
                f"Google step {step_index} currently supports list/read operations for {resource_kind} reads."
            )
        if operation == "list" and action_kind == "export":
            operation = "export"
        if action_kind == "export":
            operation = "export"
        if operation == "export":
            if not raw["file_id"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires file_id for {resource_kind} export workflows."
                )
            if not raw["export_mime_type"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires export_mime_type for {resource_kind} export workflows."
                )
        elif action_kind == "read":
            if resource_kind == "drive":
                if raw["file_id"]:
                    operation = "read"
                else:
                    operation = "list"
            elif not raw["file_id"]:
                raise GoogleBridgeTaskError(
                    f"Google step {step_index} requires file_id for {resource_kind} read workflows."
                )
        if resource_kind == "drive" and raw["query"] and not raw["file_id"]:
            operation = "list"
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


def _normalize_people_field_mask(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        parts = [part.strip() for part in text.split(",") if part.strip()]
        return ",".join(parts)
    if isinstance(value, dict):
        return _normalize_people_field_mask(list(value.values()))
    if isinstance(value, Iterable):
        parts: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                parts.extend(part.strip() for part in text.split(",") if part.strip())
        return ",".join(parts)
    text = str(value).strip()
    return text


def _normalize_people_contact_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    payload = dict(value)
    resource_name = str(payload.get("resourceName") or payload.get("resource_name") or "").strip()
    if resource_name:
        payload["resourceName"] = resource_name
    else:
        payload.pop("resourceName", None)
        payload.pop("resource_name", None)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_payload = {
            str(key): raw_value
            for key, raw_value in metadata.items()
            if raw_value not in (None, "", [], {}, ())
        }
        if metadata_payload:
            payload["metadata"] = metadata_payload
        else:
            payload.pop("metadata", None)
    return payload


def _people_contact_has_sources(person: dict[str, object]) -> bool:
    metadata = person.get("metadata")
    if not isinstance(metadata, dict):
        return False
    sources = metadata.get("sources")
    if isinstance(sources, list):
        return any(source for source in sources)
    return bool(sources)


def _people_contact_has_etag(person: dict[str, object]) -> bool:
    etag = str(person.get("etag") or "").strip()
    if etag:
        return True
    metadata = person.get("metadata")
    if not isinstance(metadata, dict):
        return False
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("etag") or "").strip():
            return True
    return False


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _normalize_gmail_filter_payload(value: object) -> dict[str, object]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise GoogleBridgeTaskError("Gmail filter criteria and action must be JSON objects.")
    payload: dict[str, object] = {}
    for key, raw_value in value.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if normalized_key in {"addLabelIds", "removeLabelIds"}:
            items = _normalize_string_list(raw_value)
            if items:
                payload[normalized_key] = items
            continue
        if normalized_key in {"hasAttachment", "excludeChats"}:
            payload[normalized_key] = _normalize_bool(raw_value)
            continue
        if normalized_key == "size":
            try:
                payload[normalized_key] = int(raw_value)
            except Exception:
                continue
            continue
        text = str(raw_value or "").strip()
        if text:
            payload[normalized_key] = text
    return payload


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


def _summarize_gmail_filter_list(data: dict) -> str:
    filters = list(data.get("filter") or data.get("filters") or [])
    if not filters:
        return "No Gmail filters were returned."
    previews: list[str] = []
    for item in filters[:5]:
        filter_id = str(item.get("id") or "").strip()
        criteria = _summarize_gmail_filter_criteria(dict(item.get("criteria") or {}))
        action = _summarize_gmail_filter_action(dict(item.get("action") or {}))
        parts = []
        if filter_id:
            parts.append(filter_id)
        if criteria:
            parts.append(f"criteria={criteria}")
        if action:
            parts.append(f"action={action}")
        if parts:
            previews.append(" | ".join(parts))
    return f"Returned {len(filters)} Gmail filters. Filters: {'; '.join(previews)}."


def _summarize_gmail_filter(data: dict) -> str:
    filter_id = str(data.get("id") or "").strip()
    criteria = _summarize_gmail_filter_criteria(dict(data.get("criteria") or {}))
    action = _summarize_gmail_filter_action(dict(data.get("action") or {}))
    parts = ["Gmail filter retrieved."]
    if filter_id:
        parts.append(f"Filter ID: {filter_id}")
    if criteria:
        parts.append(f"Criteria: {criteria}")
    if action:
        parts.append(f"Action: {action}")
    return " ".join(parts)


def _summarize_gmail_filter_mutation(data: dict, *, action: str) -> str:
    filter_id = str(data.get("id") or data.get("filterId") or "").strip()
    criteria = _summarize_gmail_filter_criteria(dict(data.get("criteria") or {}))
    filter_action = _summarize_gmail_filter_action(dict(data.get("action") or {}))
    parts = [f"Gmail filter {action}."]
    if filter_id:
        parts.append(f"Filter ID: {filter_id}")
    if criteria:
        parts.append(f"Criteria: {criteria}")
    if filter_action:
        parts.append(f"Action: {filter_action}")
    return " ".join(parts)


def _summarize_gmail_filter_batch_mutation(data: dict, *, action: str) -> str:
    filters = list(data.get("filters") or [])
    parts = [f"Gmail filters {action}."]
    if filters:
        parts.append(f"Count: {len(filters)}")
    return " ".join(parts)


def _summarize_gmail_filter_preview(data: dict, *, action: str) -> str:
    previews = list(data.get("preview_filters") or [])
    parts = [f"Gmail filter preview ready."]
    if action:
        parts[0] = f"Gmail filter preview ready for {action}."
    if previews:
        parts.append(f"Count: {len(previews)}")
        preview_bits: list[str] = []
        for item in previews[:5]:
            query = str(item.get("query") or "").strip()
            count = int(item.get("resultSizeEstimate") or 0)
            preview_bits.append(f"{query or 'no query'}={count}")
        if preview_bits:
            parts.append(f"Matches: {'; '.join(preview_bits)}")
    else:
        parts.append("No criteria.query clauses were available to preview.")
    return " ".join(parts)


def _gmail_filter_candidate_criteria(criteria: dict[str, object], query_clauses: list[str]) -> list[dict[str, object]]:
    base_criteria = dict(criteria or {})
    query_text = str(base_criteria.get("query") or "").strip()
    if not query_text:
        return [base_criteria]
    clauses = [str(item or "").strip() for item in query_clauses if str(item or "").strip()]
    if not clauses:
        clauses = [query_text]
    candidates: list[dict[str, object]] = []
    for clause in clauses:
        candidate = dict(base_criteria)
        candidate["query"] = clause
        candidates.append(candidate)
    return candidates


def _summarize_gmail_filter_delete(filter_id: str) -> str:
    parts = ["Gmail filter deleted."]
    if filter_id:
        parts.append(f"Filter ID: {filter_id}")
    return " ".join(parts)


def _summarize_gmail_filter_criteria(criteria: dict[str, object]) -> str:
    parts: list[str] = []
    for field in ("from", "to", "subject", "query", "negatedQuery", "sizeComparison"):
        value = str(criteria.get(field) or "").strip()
        if value:
            parts.append(f"{field}={value}")
    if criteria.get("hasAttachment") is not None:
        parts.append(f"hasAttachment={bool(criteria.get('hasAttachment'))}")
    if criteria.get("excludeChats") is not None:
        parts.append(f"excludeChats={bool(criteria.get('excludeChats'))}")
    size = criteria.get("size")
    if size not in (None, ""):
        parts.append(f"size={size}")
    return ", ".join(parts)


def _summarize_gmail_filter_action(action: dict[str, object]) -> str:
    parts: list[str] = []
    add_label_ids = _normalize_string_list(action.get("addLabelIds"))
    if add_label_ids:
        parts.append(f"addLabelIds={','.join(add_label_ids)}")
    remove_label_ids = _normalize_string_list(action.get("removeLabelIds"))
    if remove_label_ids:
        parts.append(f"removeLabelIds={','.join(remove_label_ids)}")
    forward = str(action.get("forward") or "").strip()
    if forward:
        parts.append(f"forward={forward}")
    return ", ".join(parts)


def _build_gmail_filter_preview(
    client: GoogleBridgeClient,
    *,
    normalized: dict[str, object],
    query_clauses: list[str],
) -> dict[str, object]:
    preview_limit = max(1, int(normalized.get("preview_max_results") or 5))
    clause_calls: list[dict[str, object]] = []
    for clause in query_clauses:
        clause_text = str(clause or "").strip()
        if not clause_text:
            continue
        clause_plan = _plan_google_query(
            clause_text,
            resource_kind="gmail",
            action_kind="read",
            operation="list",
        )
        clause_calls.extend(list(clause_plan.to_dict().get("calls") or []))
    shared_corpus_plan = _gmail_shared_corpus_plan_from_calls(clause_calls)
    if shared_corpus_plan is not None:
        base_query, clause_remainders = shared_corpus_plan
        page = client.list_gmail_messages(
            query=base_query,
            label_ids=[],
            max_results=max(preview_limit * 10, 100),
        )
        preview_data = _enrich_gmail_list_messages(
            client,
            {
                "messages": list(page.get("messages") or []),
                "resultSizeEstimate": int(page.get("resultSizeEstimate") or len(list(page.get("messages") or []))),
            },
        )
        messages = list(preview_data.get("messages") or [])
        previews: list[dict[str, object]] = []
        for clause_literals in clause_remainders:
            matched_messages = [message for message in messages if _gmail_message_matches_literals(message, clause_literals)]
            previews.append(
                {
                    "query": _render_clause(clause_literals, resource_kind="gmail"),
                    "resultSizeEstimate": len(matched_messages),
                    "messages": matched_messages[:preview_limit],
                }
            )
        return {
            "preview_filters": previews,
            "resultSizeEstimate": sum(int(item.get("resultSizeEstimate") or 0) for item in previews),
            "shared_base_query": base_query,
        }

    previews: list[dict[str, object]] = []
    for clause in query_clauses:
        if not clause:
            continue
        page = client.list_gmail_messages(
            query=clause,
            label_ids=[],
            max_results=preview_limit,
        )
        preview_data = _enrich_gmail_list_messages(
            client,
            {
                "messages": list(page.get("messages") or []),
                "resultSizeEstimate": int(page.get("resultSizeEstimate") or len(list(page.get("messages") or []))),
            },
        )
        messages = list(preview_data.get("messages") or [])
        previews.append(
            {
                "query": clause,
                "resultSizeEstimate": int(preview_data.get("resultSizeEstimate") or len(messages)),
                "messages": messages[:preview_limit],
            }
        )
    if not previews:
        return {
            "preview_max_results": preview_limit,
            "preview_filters": [],
            "query_plan": normalized.get("_gmail_filter_query_plan") or {},
        }
    return {
        "preview_max_results": preview_limit,
        "preview_filters": previews,
        "query_plan": normalized.get("_gmail_filter_query_plan") or {},
    }


def _normalize_gmail_message_format(
    normalized: dict[str, object],
    *,
    include_body: bool,
    include_html: bool,
    include_raw: bool,
    include_attachments: bool,
) -> str:
    requested = str(normalized.get("format") or "").strip().lower()
    if requested == "raw":
        if include_body or include_html or include_attachments:
            return "full"
        return "raw"
    if requested in {"metadata", "full", "minimal"}:
        if requested == "metadata" and (include_body or include_html or include_attachments):
            return "full"
        return requested
    if include_raw:
        if include_body or include_html or include_attachments:
            return "full"
        return "raw"
    if include_body or include_html or include_attachments:
        return "full"
    return "metadata"


def _expand_gmail_message_parts(
    client: GoogleBridgeClient,
    data: dict,
    *,
    message_id: str,
    include_body: bool,
    include_html: bool,
    include_attachments: bool,
) -> dict:
    expanded = dict(data)
    payload = dict(expanded.get("payload") or {})
    if not payload:
        return expanded
    text_parts, html_parts, attachments = _collect_gmail_message_parts(
        client,
        payload,
        message_id=message_id,
        include_attachments=include_attachments,
    )
    if include_body and text_parts:
        expanded["body_text"] = "\n\n".join(part for part in text_parts if part.strip())
    if include_html and html_parts:
        expanded["body_html"] = "\n\n".join(part for part in html_parts if part.strip())
    if attachments:
        expanded["attachments"] = attachments
    expanded["payload"] = payload
    return expanded


def _expand_gmail_raw_message(
    data: dict,
    *,
    include_body: bool,
    include_html: bool,
    include_attachments: bool,
) -> dict:
    expanded = dict(data)
    raw_source = str(expanded.pop("raw", "") or "").strip()
    if not raw_source:
        return expanded
    raw_bytes = _decode_gmail_message_bytes(raw_source)
    if not raw_bytes:
        return expanded
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    headers = [
        {"name": str(name), "value": str(value)}
        for name, value in message.items()
    ]
    expanded["payload"] = {
        "mimeType": message.get_content_type(),
        "headers": headers,
    }
    text_parts, html_parts, attachments = _collect_raw_email_parts(
        message,
        include_body=include_body,
        include_html=include_html,
        include_attachments=include_attachments,
    )
    if include_body and text_parts:
        expanded["body_text"] = "\n\n".join(part for part in text_parts if part.strip())
    if include_html and html_parts:
        expanded["body_html"] = "\n\n".join(part for part in html_parts if part.strip())
    if attachments:
        expanded["attachments"] = attachments
    expanded["raw_source_bytes"] = len(raw_bytes)
    return expanded


def _collect_gmail_message_parts(
    client: GoogleBridgeClient,
    payload: dict,
    *,
    message_id: str,
    include_attachments: bool,
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, object]] = []

    def visit(part: dict[str, object]) -> None:
        mime_type = str(part.get("mimeType") or "").strip().lower()
        filename = str(part.get("filename") or "").strip()
        body = dict(part.get("body") or {})
        data = str(body.get("data") or "").strip()
        attachment_id = str(body.get("attachmentId") or "").strip()
        part_id = str(part.get("partId") or "").strip()

        if data and mime_type == "text/plain":
            text_parts.append(_decode_gmail_message_data(data))
        elif data and mime_type == "text/html":
            html_parts.append(_decode_gmail_message_data(data))
        elif data and mime_type.startswith("text/"):
            text_parts.append(_decode_gmail_message_data(data))

        if include_attachments and (filename or attachment_id):
            attachment_info: dict[str, object] = {
                "part_id": part_id,
                "mime_type": mime_type,
                "filename": filename,
                "attachment_id": attachment_id,
                "size": body.get("size"),
            }
            if attachment_id:
                try:
                    attachment_data = client.get_gmail_attachment(message_id, attachment_id)
                except Exception:
                    attachment_data = {}
                attachment_body = str(attachment_data.get("data") or "").strip()
                if attachment_body and mime_type.startswith("text/"):
                    attachment_info["text"] = _decode_gmail_message_data(attachment_body)
            attachments.append({key: value for key, value in attachment_info.items() if value not in (None, "", [], {}, ())})

        for child in list(part.get("parts") or []):
            if isinstance(child, dict):
                visit(child)

    visit(payload)
    return text_parts, html_parts, attachments


def _collect_raw_email_parts(
    message,
    *,
    include_body: bool,
    include_html: bool,
    include_attachments: bool,
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, object]] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        mime_type = str(part.get_content_type() or "").strip().lower()
        filename = str(part.get_filename() or "").strip()
        content_disposition = str(part.get_content_disposition() or "").strip().lower()
        content = part.get_content()
        if include_body and mime_type == "text/plain":
            text_parts.append(str(content or "").strip())
        elif include_html and mime_type == "text/html":
            html_parts.append(str(content or "").strip())
        elif include_body and mime_type.startswith("text/") and content_disposition != "attachment":
            text_parts.append(str(content or "").strip())
        if include_attachments and (content_disposition == "attachment" or filename):
            attachment_info: dict[str, object] = {
                "mime_type": mime_type,
                "filename": filename,
                "content_disposition": content_disposition,
            }
            if mime_type.startswith("text/"):
                attachment_info["text"] = str(content or "").strip()
            else:
                payload = part.get_payload(decode=True) or b""
                attachment_info["size"] = len(payload)
            attachments.append({key: value for key, value in attachment_info.items() if value not in (None, "", [], {}, ())})

    return text_parts, html_parts, attachments


def _decode_gmail_message_bytes(data: str) -> bytes:
    text = str(data or "").strip()
    if not text:
        return b""
    padding = "=" * (-len(text) % 4)
    try:
        return urlsafe_b64decode(text + padding)
    except Exception:
        return b""


def _decode_gmail_message_data(data: str) -> str:
    text = str(data or "").strip()
    if not text:
        return ""
    padding = "=" * (-len(text) % 4)
    try:
        decoded = urlsafe_b64decode(text + padding)
    except Exception:
        return text
    return decoded.decode("utf-8", errors="replace")


def _compact_message_preview(text: str, *, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", str(text or "").strip())
    if len(compact) > limit:
        return compact[:limit].rstrip() + "..."
    return compact


def _html_to_text_preview(text: str, *, limit: int = 500) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", str(text or ""))
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</p\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    return _compact_message_preview(cleaned, limit=limit)


def _summarize_gmail_message(data: dict) -> str:
    headers = {str(header.get("name") or "").lower(): str(header.get("value") or "").strip() for header in data.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    snippet = str(data.get("snippet") or "").strip()
    body_text = _compact_message_preview(str(data.get("body_text") or "").strip())
    body_html = _html_to_text_preview(str(data.get("body_html") or "").strip())
    attachments = list(data.get("attachments") or [])
    parts = ["Gmail message retrieved."]
    if subject:
        parts.append(f"Subject: {subject}")
    if sender:
        parts.append(f"From: {sender}")
    if snippet:
        parts.append(f"Snippet: {snippet}")
    if body_text:
        parts.append(f"Body: {body_text}")
    elif body_html:
        parts.append(f"Body: {body_html}")
    if attachments:
        attachment_names = ", ".join(
            _compact_message_preview(str(item.get("filename") or item.get("attachment_id") or ""), limit=80)
            for item in attachments[:5]
            if str(item.get("filename") or item.get("attachment_id") or "").strip()
        )
        if attachment_names:
            parts.append(f"Attachments: {len(attachments)} ({attachment_names})")
        else:
            parts.append(f"Attachments: {len(attachments)}")
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


def _summarize_drive_list(data: dict, *, query: str = "") -> str:
    files = list(data.get("files") or [])
    if not files:
        return "No Drive files were returned." if not query else f"No Drive files matched query '{query}'."
    previews: list[str] = []
    for item in files[:5]:
        file_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        mime_type = str(item.get("mimeType") or "").strip()
        modified_time = str(item.get("modifiedTime") or "").strip()
        parts = []
        if file_id:
            parts.append(file_id)
        if name:
            parts.append(f"name={name}")
        if mime_type:
            parts.append(f"mimeType={mime_type}")
        if modified_time:
            parts.append(f"modified={modified_time}")
        if parts:
            previews.append(" | ".join(parts))
    if query:
        return f"Returned {len(files)} Drive files for query '{query}'. Files: {'; '.join(previews)}."
    return f"Returned {len(files)} Drive files. Files: {'; '.join(previews)}."


def _summarize_people_connections(data: dict) -> str:
    connections = list(data.get("connections") or [])
    if not connections:
        return "No Google contacts were returned."
    previews: list[str] = []
    for item in connections[:5]:
        person = dict(item or {})
        parts = _summarize_people_person_parts(person)
        if parts:
            previews.append(" | ".join(parts))
    return f"Returned {len(connections)} Google contacts. Contacts: {'; '.join(previews)}."


def _summarize_people_search(data: dict) -> str:
    results = list(data.get("results") or [])
    query = str(data.get("query") or "").strip()
    if not results:
        return "No Google contacts matched the search query." if not query else f"No Google contacts matched query '{query}'."
    previews: list[str] = []
    for item in results[:5]:
        person = _extract_people_person(item)
        parts = _summarize_people_person_parts(person)
        if parts:
            previews.append(" | ".join(parts))
    if query:
        return f"Returned {len(results)} Google contacts for query '{query}'. Contacts: {'; '.join(previews)}."
    return f"Returned {len(results)} Google contacts. Contacts: {'; '.join(previews)}."


def _summarize_people_person(data: dict) -> str:
    parts = _summarize_people_person_parts(dict(data or {}))
    if not parts:
        return "Google contact retrieved."
    return "Google contact retrieved. " + " ".join(parts)


def _summarize_people_person_action(data: dict, *, action: str) -> str:
    parts = _summarize_people_person_parts(dict(data or {}))
    if not parts:
        return f"Google contact {action}."
    return f"Google contact {action}. " + " ".join(parts)


def _summarize_people_person_parts(person: dict[str, object]) -> list[str]:
    parts: list[str] = []
    resource_name = _people_resource_name(person)
    if resource_name:
        parts.append(f"Resource: {resource_name}")
    display_name = _people_display_name(person)
    if display_name:
        parts.append(f"Name: {display_name}")
    email = _people_primary_email(person)
    if email:
        parts.append(f"Email: {email}")
    phone = _people_primary_phone(person)
    if phone:
        parts.append(f"Phone: {phone}")
    return parts


def _extract_people_person(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        person = item.get("person")
        if isinstance(person, dict):
            return dict(person)
        return dict(item)
    return {}


def _people_resource_name(person: dict[str, object]) -> str:
    return str(person.get("resourceName") or person.get("resource_name") or "").strip()


def _people_display_name(person: dict[str, object]) -> str:
    names = person.get("names") or []
    if isinstance(names, list):
        for item in names:
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("displayName") or "").strip()
            if display_name:
                return display_name
    return str(person.get("displayName") or "").strip()


def _people_primary_email(person: dict[str, object]) -> str:
    emails = person.get("emailAddresses") or []
    if isinstance(emails, list):
        for item in emails:
            if not isinstance(item, dict):
                continue
            address = str(item.get("value") or "").strip()
            if address:
                return address
    return ""


def _people_primary_phone(person: dict[str, object]) -> str:
    phones = person.get("phoneNumbers") or []
    if isinstance(phones, list):
        for item in phones:
            if not isinstance(item, dict):
                continue
            number = str(item.get("value") or "").strip()
            if number:
                return number
    return ""


def _summarize_drive_file(data: dict) -> str:
    file_id = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    mime_type = str(data.get("mimeType") or "").strip()
    parts = ["Drive file retrieved."]
    if file_id:
        parts.append(f"File ID: {file_id}")
    if name:
        parts.append(f"Name: {name}")
    if mime_type:
        parts.append(f"MIME type: {mime_type}")
    return " ".join(parts)


def _summarize_docs_document(data: dict) -> str:
    title = str(data.get("title") or "").strip()
    body_text = _extract_document_text(data)
    parts = ["Google Doc retrieved."]
    if title:
        parts.append(f"Title: {title}")
    if body_text:
        parts.append(f"Preview: {body_text[:240]}")
    return " ".join(parts)


def _summarize_docs_export(file_id: str, mime_type: str, content: bytes) -> str:
    text = _decode_text_bytes(content, mime_type)
    parts = ["Google Doc exported."]
    if file_id:
        parts.append(f"File ID: {file_id}")
    if mime_type:
        parts.append(f"Export MIME type: {mime_type}")
    if text:
        parts.append(f"Preview: {text[:240]}")
    else:
        parts.append(f"Bytes: {len(content)}")
    return " ".join(parts)


def _summarize_sheet_values(data: dict, *, spreadsheet_id: str, range_name: str = "") -> str:
    values = list(data.get("values") or [])
    if not values:
        label = range_name or "Sheet1"
        return f"No Sheet values were returned for {label}."
    preview_rows: list[str] = []
    for row in values[:5]:
        preview_rows.append(" | ".join(str(cell) for cell in row[:6]))
    parts = ["Google Sheet values retrieved."]
    if spreadsheet_id:
        parts.append(f"Spreadsheet ID: {spreadsheet_id}")
    if range_name:
        parts.append(f"Range: {range_name}")
    parts.append(f"Preview: {'; '.join(preview_rows)}")
    return " ".join(parts)


def _summarize_sheet_export(file_id: str, mime_type: str, content: bytes) -> str:
    text = _decode_text_bytes(content, mime_type)
    parts = ["Google Sheet exported."]
    if file_id:
        parts.append(f"File ID: {file_id}")
    if mime_type:
        parts.append(f"Export MIME type: {mime_type}")
    if text:
        parts.append(f"Preview: {text[:240]}")
    else:
        parts.append(f"Bytes: {len(content)}")
    return " ".join(parts)


def _extract_document_text(data: dict) -> str:
    parts: list[str] = []

    def walk(nodes: object) -> None:
        if isinstance(nodes, dict):
            text = str(nodes.get("content") or nodes.get("text") or "")
            if text.strip():
                parts.append(text.strip())
            for value in nodes.values():
                walk(value)
        elif isinstance(nodes, list):
            for item in nodes:
                walk(item)

    walk(data.get("body") or data.get("bodyContent") or data)
    return " ".join(parts).strip()


def _decode_text_bytes(content: bytes, mime_type: str) -> str:
    if not content:
        return ""
    lowered = str(mime_type or "").lower()
    if "text" in lowered or lowered in {"application/json", "application/xml", "text/csv", "text/plain", "text/tab-separated-values"}:
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return content.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
    return ""


def _encode_bytes_base64(content: bytes) -> str:
    if not content:
        return ""
    import base64

    return base64.b64encode(content).decode("ascii")


def _resolve_google_file_id(normalized: dict[str, object]) -> str:
    for key in ("file_id", "document_id", "spreadsheet_id", "message_id", "event_id"):
        text = str(normalized.get(key) or "").strip()
        if text:
            return text
    return ""


def _gmail_or_clause_limit() -> int:
    return _google_query_clause_limit()


def _expand_gmail_query_clauses(query: str) -> list[str]:
    return _google_query_clauses({"query": query})


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


def _plan_google_query(query: str, *, resource_kind: str, action_kind: str, operation: str):
    try:
        return plan_google_query(
            query,
            resource_kind=resource_kind,
            action_kind=action_kind,
            operation=operation,
        )
    except (QueryLanguageError, QueryPlannerError) as exc:
        raise GoogleBridgeTaskError(str(exc)) from exc


def _google_query_clause_limit() -> int:
    try:
        return max(1, int(getattr(settings, "GOOGLE_BRIDGE_QUERY_CLAUSE_LIMIT", None) or getattr(settings, "GMAIL_OR_CLAUSE_LIMIT", 10) or 10))
    except Exception:
        return 10


def _google_query_clauses(normalized: dict[str, object]) -> list[str]:
    stored_plan = normalized.get("_google_query_plan")
    if isinstance(stored_plan, dict):
        stored_calls = list(stored_plan.get("calls") or [])
        return [str(item.get("query") or "") for item in stored_calls]
    query = str(normalized.get("query") or "").strip()
    if not query:
        return [""]
    plan = _plan_google_query(
        query,
        resource_kind=str(normalized.get("resource_kind") or "gmail"),
        action_kind=str(normalized.get("action_kind") or "read"),
        operation=str(normalized.get("operation") or "list"),
    )
    return list(plan.query_strings)


_GMAIL_SHARED_CORPUS_MIN_CLAUSES = 5
_GMAIL_SHARED_CORPUS_FIELDS = {"from", "to", "subject", "label_ids", "in", "is", "newer_than", "older_than"}
_GMAIL_SHARED_CORPUS_IN_VALUES = {"anywhere", "all", "inbox", "trash", "spam", "sent", "drafts", "snoozed", "important", "starred", "unread", "promotions", "social"}
_GMAIL_SHARED_CORPUS_IS_VALUES = {"read", "unread", "starred", "important", "snoozed"}
_GMAIL_SHARED_CORPUS_DATE_PATTERN = re.compile(r"^\s*(\d+)\s*([dhmw])\s*$", re.IGNORECASE)
_GMAIL_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+")


def _gmail_query_plan_calls(normalized: dict[str, object]) -> list[dict[str, object]]:
    stored_plan = normalized.get("_google_query_plan")
    if isinstance(stored_plan, dict):
        calls = list(stored_plan.get("calls") or [])
        if calls:
            return [dict(item) for item in calls]
    query = str(normalized.get("query") or "").strip()
    if not query:
        return []
    plan = _plan_google_query(
        query,
        resource_kind=str(normalized.get("resource_kind") or "gmail"),
        action_kind=str(normalized.get("action_kind") or "read"),
        operation=str(normalized.get("operation") or "list"),
    )
    return [dict(item) for item in plan.to_dict().get("calls") or []]


def _gmail_literal_from_dict(item: dict[str, object]) -> QueryLiteral:
    return QueryLiteral(
        field_name=str(item.get("field_name") or "").strip() or None,
        value=str(item.get("value") or ""),
        negated=bool(item.get("negated")),
        operator=str(item.get("operator") or "").strip() or None,
    )


def _gmail_clause_literals_from_call(call: dict[str, object]) -> tuple[QueryLiteral, ...]:
    return tuple(_gmail_literal_from_dict(item) for item in list(call.get("literals") or []))


def _gmail_clause_key(literal: QueryLiteral) -> tuple[str, str, bool, str]:
    return (
        str(literal.field_name or ""),
        str(literal.value or ""),
        bool(literal.negated),
        str(literal.operator or ""),
    )


def _gmail_clause_literals_locally_matchable(literals: tuple[QueryLiteral, ...]) -> bool:
    for literal in literals:
        field_name = str(literal.field_name or "").strip().lower()
        if field_name not in _GMAIL_SHARED_CORPUS_FIELDS and field_name != "":
            return False
    return True


def _gmail_shared_corpus_plan_from_calls(calls: list[dict[str, object]]) -> tuple[str, list[tuple[QueryLiteral, ...]]] | None:
    if len(calls) < _GMAIL_SHARED_CORPUS_MIN_CLAUSES:
        return None
    clause_literals = [_gmail_clause_literals_from_call(call) for call in calls]
    if not clause_literals:
        return None
    if not all(_gmail_clause_literals_locally_matchable(literals) for literals in clause_literals):
        return None
    clause_key_sets = [set(_gmail_clause_key(literal) for literal in literals) for literals in clause_literals]
    shared_keys = set.intersection(*clause_key_sets) if clause_key_sets else set()
    shared_literals = tuple(
        literal
        for literal in clause_literals[0]
        if _gmail_clause_key(literal) in shared_keys
    )
    shared_set = {_gmail_clause_key(literal) for literal in shared_literals}
    clause_remainders: list[tuple[QueryLiteral, ...]] = []
    for literals in clause_literals:
        remainder = tuple(literal for literal in literals if _gmail_clause_key(literal) not in shared_set)
        clause_remainders.append(remainder)
    base_query = _render_clause(shared_literals, resource_kind="gmail")
    return base_query, clause_remainders


def _gmail_shared_corpus_plan(normalized: dict[str, object]) -> tuple[str, list[tuple[QueryLiteral, ...]]] | None:
    return _gmail_shared_corpus_plan_from_calls(_gmail_query_plan_calls(normalized))


def _gmail_message_text(message: dict[str, object]) -> str:
    parts = [
        str(message.get("subject") or "").strip(),
        str(message.get("from") or "").strip(),
        str(message.get("to") or "").strip(),
        str(message.get("snippet") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def _gmail_message_emails(message: dict[str, object], field_name: str) -> list[str]:
    value = str(message.get(field_name) or "").strip()
    if not value:
        return []
    return [match.lower() for match in _GMAIL_EMAIL_PATTERN.findall(value)]


def _gmail_message_labels(message: dict[str, object]) -> set[str]:
    labels = list(message.get("labelIds") or message.get("label_ids") or [])
    return {str(label or "").strip().lower() for label in labels if str(label or "").strip()}


def _gmail_message_datetime(message: dict[str, object]) -> datetime | None:
    date_text = str(message.get("date") or "").strip()
    if not date_text:
        return None
    try:
        parsed = parsedate_to_datetime(date_text)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _gmail_relative_delta(value: str) -> timedelta | None:
    match = _GMAIL_SHARED_CORPUS_DATE_PATTERN.match(value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    return None


def _gmail_message_matches_literal(message: dict[str, object], literal: QueryLiteral) -> bool:
    field_name = str(literal.field_name or "").strip().lower()
    value = str(literal.value or "").strip()
    if not value and field_name != "in":
        return False
    if field_name == "from":
        emails = _gmail_message_emails(message, "from")
        sender_text = _gmail_message_text(message).lower()
        candidate = value.lower()
        if "@" in candidate:
            return any(email == candidate for email in emails) or candidate in sender_text
        return any(email.endswith(f"@{candidate}") or email.endswith(candidate) for email in emails) or candidate in sender_text
    if field_name == "to":
        emails = _gmail_message_emails(message, "to")
        recipient_text = _gmail_message_text(message).lower()
        candidate = value.lower()
        if "@" in candidate:
            return any(email == candidate for email in emails) or candidate in recipient_text
        return any(email.endswith(f"@{candidate}") or email.endswith(candidate) for email in emails) or candidate in recipient_text
    if field_name == "subject":
        return value.lower() in str(message.get("subject") or "").lower()
    if field_name == "label_ids":
        labels = _gmail_message_labels(message)
        candidates = [item.strip().lower() for item in re.split(r"[,\s]+", value) if item.strip()]
        return any(candidate in labels for candidate in candidates)
    if field_name == "in":
        candidate = value.lower()
        labels = _gmail_message_labels(message)
        if candidate in {"anywhere", "all"}:
            return True
        label_map = {
            "inbox": "inbox",
            "trash": "trash",
            "spam": "spam",
            "sent": "sent",
            "drafts": "draft",
            "snoozed": "snoozed",
            "important": "important",
            "starred": "starred",
            "promotions": "category_promotions",
            "social": "category_social",
        }
        label_name = label_map.get(candidate, candidate)
        return label_name in labels
    if field_name == "is":
        candidate = value.lower()
        labels = _gmail_message_labels(message)
        if candidate == "read":
            return "unread" not in labels
        if candidate == "unread":
            return "unread" in labels
        return candidate in labels
    if field_name == "newer_than":
        delta = _gmail_relative_delta(value)
        message_dt = _gmail_message_datetime(message)
        if delta is None or message_dt is None:
            return False
        return message_dt >= timezone.now() - delta
    if field_name == "older_than":
        delta = _gmail_relative_delta(value)
        message_dt = _gmail_message_datetime(message)
        if delta is None or message_dt is None:
            return False
        return message_dt <= timezone.now() - delta
    if field_name == "":
        return value.lower() in _gmail_message_text(message).lower()
    return False


def _gmail_message_matches_literals(message: dict[str, object], literals: tuple[QueryLiteral, ...]) -> bool:
    if not literals:
        return True
    for literal in literals:
        matched = _gmail_message_matches_literal(message, literal)
        if literal.negated:
            matched = not matched
        if not matched:
            return False
    return True


def _gmail_message_matches_any_clause(message: dict[str, object], clause_literals: list[tuple[QueryLiteral, ...]]) -> bool:
    return any(_gmail_message_matches_literals(message, literals) for literals in clause_literals)


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
                "to": headers.get("to", ""),
                "date": headers.get("date", ""),
                "snippet": str(message_data.get("snippet") or "").strip(),
                "labelIds": list(message_data.get("labelIds") or message.get("labelIds") or []),
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
