from __future__ import annotations

from copy import deepcopy

GOOGLE_BRIDGE_TOOL_NAME = "google_bridge"
GOOGLE_BRIDGE_TOOL_GROUP_NAME = "Google Bridge"
GOOGLE_BRIDGE_TOOL_DESCRIPTION = (
    "Read and write Google Gmail and Calendar data through a JSON bridge, with Gmail draft/send workflows supported. "
    "Use this tool directly for live reads and Gmail draft/send/trash/delete workflows; the same payload shape is reused by scheduled headless runs. "
    "Gmail draft, send, trash, and delete workflows are supported. Preferred Gmail write flow is to create a draft first, then send that draft when ready. Preferred Gmail deletion flow is to trash first unless permanent deletion is explicitly intended. Calendar create, update, and delete workflows are supported. Use local time for Calendar inputs, and keep the same time zone convention across create/update calls. "
    "For Gmail, bare list operations default to unread messages. Gmail list rows include message IDs, sender, subject, date, and snippet so the agent can inspect mail without a follow-up read. Use include_read=true when you want all Gmail messages instead of unread-only mail. Gmail list/read query filters support exact sender, sender domain, subject, and top-level OR splitting across those clauses. Calendar list reads inspect all calendars on the connected account unless a specific calendar_id is supplied. "
    "Use the account_email or google_subject returned by the list step when you do the follow-up read. For trash/delete, do not use read as a lookup step. Use the latest list result's message_id directly when possible. For Gmail reads, use list with query filters such as from:info@airbnb.com for exact sender search, from:airbnb.com for sender-domain search, subject:(\"Airbnb\") for subject search, and label_ids or include_read for inbox/mailbox filtering. OR is supported for Gmail list/read searches and bulk trash/delete cleanup only at the top level, where it is split into separate Gmail clauses before merging or deleting. Nested OR inside parentheses is rejected as malformed. Trash is the safe default; set delete_mode=delete only when you explicitly want permanent deletion. When deleting many messages by query, use action_kind=delete with operation=trash (or omit operation and let it default) plus the appropriate Gmail query. If you need multiple cleanup targets in one call, you may join them with ` OR ` and the bridge will split them into separate Gmail delete clauses as long as each clause is a complete top-level search. The bridge caps Gmail OR fan-out at 10 top-level clauses by default; set `TOOLRUNNER_GMAIL_OR_CLAUSE_LIMIT` to adjust it. If the agent accidentally writes `from:@domain.com` or adds stray spaces after query tokens, the bridge normalizes that to the canonical Gmail form. Use account_scope=all when you want that query-based cleanup to fan out across every active connected account in the workspace. "
    "Calendar queries are local-time oriented: use the user's local time first, not GMT or Zulu, unless you convert from local time before querying. "
    "If a timezone is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`. "
    "If the user's local time is unknown, assume Eastern Time (EST/EDT as appropriate for daylight savings). "
    "The contract is intentionally forward-compatible so future Google surfaces like People, Places, Drive, and Sheets can reuse the same bridge pattern."
)


def _google_bridge_step_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "integration_kind": {
                "type": "string",
                "enum": ["google"],
                "default": "google",
            },
            "resource_kind": {
                "type": "string",
                "description": "Current supported values: gmail, calendar. Gmail supports draft/send writes and Calendar supports create/update/delete writes. Future values may include people, places, drive, and sheets.",
            },
            "action_kind": {
                "type": "string",
                "enum": ["read", "list", "draft", "send", "create", "update", "delete"],
                "default": "read",
                "description": "Current supported values: read, list, draft, send, create, update, delete. List is accepted as a read synonym for Gmail and Calendar list workflows. Draft and send are supported for Gmail writes. Create, update, and delete are supported for Calendar writes. Delete is supported for Gmail trash/delete workflows. Preferred Gmail write flow is draft first, then send the draft when ready. Preferred Gmail deletion flow is trash first unless permanent deletion is explicitly intended.",
            },
            "operation": {
                "type": "string",
                "enum": ["list", "read", "create", "update", "send", "trash", "delete"],
                "default": "list",
                "description": "Current supported values: read, list, draft, send, create, update, delete. Use read/list with query filters for Gmail searches: from:info@airbnb.com for exact sender, from:airbnb.com for sender-domain, subject:(\"Airbnb\") for subject, plus label_ids or include_read for mailbox filtering. Use create for Gmail drafts or Calendar creates, update for Calendar edits, send for Gmail delivery, trash to move a Gmail message to trash, and delete for permanent Gmail or Calendar deletion. For Gmail delete workflows, trash is the default safe behavior; set delete_mode=delete only when you explicitly want permanent deletion. OR is supported for Gmail list/read searches and bulk trash/delete cleanup only at the top level, where it is split into separate Gmail clauses before merging or deleting. Nested OR inside parentheses is rejected as malformed. For bulk cleanup, use the query that matches your intent: subject:(\"Airbnb\") for subject-based cleanup, from:info@airbnb.com for exact sender cleanup, and from:airbnb.com for sender-domain cleanup. In each case, use action_kind=delete with operation=trash (or omit operation and let it default) for trash-first cleanup, or operation=delete / delete_mode=delete for permanent deletion. If you need multiple cleanup targets in one call, you may join them with OR and the bridge will split them into separate Gmail clauses as long as each clause is a complete top-level search. The bridge caps Gmail OR fan-out at 10 top-level clauses by default; set TOOLRUNNER_GMAIL_OR_CLAUSE_LIMIT to adjust it. Use account_scope=all when you want that query-based cleanup to fan out across every active connected account in the workspace. For Gmail writes, create the draft first, then send that draft by supplying draft_id.",
            },
            "account_scope": {
                "type": "string",
                "enum": ["primary", "all"],
                "default": "primary",
                "description": "Primary uses the best matching connected account. All fans out across every active connected account in the workspace and merges the results into one response. Use all when you want to check every connected Gmail inbox or calendar at once.",
            },
            "email": {
                "type": "string",
                "description": "Optional connected account email to target explicitly.",
            },
            "google_subject": {
                "type": "string",
                "description": "Optional Google account subject identifier to target explicitly. Do not use this for a Gmail message subject; use message_id for message-level actions and use the list step's account_email/google_subject to name the connected account. If you accidentally pass an email address here, the bridge will treat it as the account email fallback.",
            },
            "query": {
                "type": "string",
                "description": "Gmail search query string. Use Gmail list to collect message IDs, sender, subject, date, and snippet. Bare Gmail list defaults to unread messages when no query or label filter is supplied. For Gmail reads, use query filters such as from:info@airbnb.com for exact sender, from:airbnb.com for sender-domain, subject:(\"Airbnb\") for subject search, plus label_ids or include_read for mailbox filtering. Gmail list/read also supports top-level OR splitting across those clauses, so you can combine multiple sender, sender-domain, or subject filters in one read query. Nested OR inside parentheses is rejected as malformed. For trash/delete, do not use read as a lookup step. OR is also supported for bulk trash/delete cleanup at the top level, where it is split into separate delete clauses. When deleting by query, action_kind=delete with operation=trash is the safe default. Use account_scope=all when you want the same query to apply across every active connected account. If you need multiple cleanup targets in one call, join them with ` OR ` and the bridge will split them into separate Gmail delete clauses as long as each clause is complete. Gmail OR fan-out is capped at 10 top-level clauses by default; set TOOLRUNNER_GMAIL_OR_CLAUSE_LIMIT to adjust it. If the agent accidentally writes `from:@domain.com` or adds stray spaces after query tokens, the bridge normalizes that to the canonical Gmail form.",
            },
            "include_read": {
                "type": "boolean",
                "default": False,
                "description": "Set true to list all Gmail messages, including read mail. Leave false to keep the default unread-only Gmail list behavior.",
            },
            "to": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Planned Gmail mutation field. Recipients for draft/send workflows; use email addresses only.",
            },
            "cc": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Planned Gmail mutation field. Optional CC recipients for draft/send workflows.",
            },
            "bcc": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Planned Gmail mutation field. Optional BCC recipients for draft/send workflows.",
            },
            "subject": {
                "type": "string",
                "description": "Planned Gmail mutation field. Subject line for draft/send workflows.",
            },
            "body": {
                "type": "string",
                "description": "Planned Gmail mutation field. Message body for draft/send workflows.",
            },
            "thread_id": {
                "type": "string",
                "description": "Planned Gmail mutation field. Thread identifier for replies, draft updates, and send workflows.",
            },
            "draft_id": {
                "type": "string",
                "description": "Planned Gmail mutation field. Draft identifier for update or send-from-draft workflows. Use this after creating a draft when you want the agent to send the prepared message.",
            },
            "delete_mode": {
                "type": "string",
                "enum": ["trash", "delete"],
                "description": "Planned Gmail mutation field. Use trash for the safe default and delete for permanent deletion. For bulk cleanup, leave delete_mode unset to trash matching messages unless you explicitly want permanent deletion. The delete_mode is only needed when you want to force a permanent delete.",
            },
            "label_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional Gmail label id filters.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "default": 20,
                "description": "Maximum rows to return for Gmail list or Calendar list. For Gmail, list defaults to unread messages and returns message IDs plus sender, subject, date, and snippet; set include_read=true to include already-read mail. Gmail list/read will page through results until this cap is reached, so you may set any positive number.",
            },
            "message_id": {
                "type": "string",
                "description": "Optional Gmail message identifier for read, trash, or delete operations. Use this after a Gmail list call to fetch subject, from, date, snippet, and metadata for one message. Pair it with the account_email or google_subject returned by the list step. For trash/delete, use the latest list result's message_id directly whenever possible; only fall back to a unique query when it identifies exactly one message.",
            },
            "calendar_id": {
                "type": "string",
                "description": "Optional calendar identifier. Calendar list queries use local-time bounds. If you omit calendar_id for a Calendar list read, the bridge queries all calendars on the connected account. For create/update/delete writes, this targets the specific calendar. Use primary for the main calendar or all to request all calendars when listing.",
            },
            "summary": {
                "type": "string",
                "description": "Calendar mutation field. Event title for create/update workflows.",
            },
            "description": {
                "type": "string",
                "description": "Calendar mutation field. Event description for create/update workflows.",
            },
            "location": {
                "type": "string",
                "description": "Calendar mutation field. Event location for create/update workflows.",
            },
            "start": {
                "type": "string",
                "description": "Calendar mutation field. Event start in local time unless an explicit offset is supplied. Use the user's local time zone when one is known; otherwise the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`.",
            },
            "end": {
                "type": "string",
                "description": "Calendar mutation field. Event end in local time unless an explicit offset is supplied. Use the user's local time zone when one is known; otherwise the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`.",
            },
            "time_zone": {
                "type": "string",
                "description": "Calendar mutation field. IANA time zone name for calendar writes. Use the user's local time zone; if unknown, default to the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`.",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Calendar mutation field. Attendee email addresses for create/update invite workflows.",
            },
            "send_updates": {
                "type": "string",
                "description": "Calendar mutation field. Google Calendar update notification behavior for invite workflows. Use all, externalOnly, or none.",
            },
            "time_min": {
                "type": "string",
                "description": "Optional calendar lower bound timestamp in local time. Do not use GMT or Zulu unless you first convert to local time. If local time is unknown, assume the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`.",
            },
            "time_max": {
                "type": "string",
                "description": "Optional calendar upper bound timestamp in local time. Do not use GMT or Zulu unless you first convert to local time. If local time is unknown, assume the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE`.",
            },
            "event_id": {
                "type": "string",
                "description": "Optional Calendar event identifier for read, update, or delete operations. Pair it with the account_email or google_subject returned by the list step.",
            },
        },
        "required": ["integration_kind", "resource_kind", "action_kind"],
    }


def build_google_bridge_args_schema() -> dict[str, object]:
    schema = _google_bridge_step_schema()
    schema["properties"]["steps"] = {
        "type": "array",
        "items": deepcopy(_google_bridge_step_schema()),
        "description": "Optional ordered step plan for multi-step Google workflows. The executor currently supports Gmail read, draft, send, and delete steps plus Calendar read, create, update, and delete steps.",
    }
    return schema


GOOGLE_BRIDGE_TOOL_EXAMPLES = [
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "read",
        "operation": "list",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "query": "in:inbox newer_than:1d",
        "max_results": 5,
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "read",
        "operation": "list",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "include_read": True,
        "max_results": 5,
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "read",
        "operation": "list",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "include_read": True,
        "query": "from:info@airbnb.com OR from:airbnb.com OR subject:(\"Airbnb\")",
        "max_results": 20,
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "read",
        "operation": "list",
        "account_scope": "all",
        "include_read": True,
        "query": "from:info@airbnb.com OR from:airbnb.com OR subject:(\"Airbnb\")",
        "max_results": 20,
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "draft",
        "operation": "create",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "to": ["someone@example.com"],
        "subject": "Draft subject",
        "body": "Draft body",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "send",
        "operation": "send",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "draft_id": "gmail-draft-id-from-create",
    },
    {
        "integration_kind": "google",
        "resource_kind": "calendar",
        "action_kind": "read",
        "operation": "list",
        "account_scope": "all",
        "calendar_id": "all",
        "time_min": "2026-03-20T00:00:00-04:00",
        "time_max": "2026-03-21T00:00:00-04:00",
        "max_results": 5,
    },
    {
        "integration_kind": "google",
        "resource_kind": "calendar",
        "action_kind": "create",
        "operation": "create",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "calendar_id": "primary",
        "summary": "Calendar write test",
        "description": "Smoke test event",
        "start": "2026-03-22T09:00:00",
        "end": "2026-03-22T09:30:00",
        "time_zone": "America/New_York",
        "attendees": ["someone@example.com"],
        "send_updates": "none",
    },
    {
        "integration_kind": "google",
        "resource_kind": "calendar",
        "action_kind": "update",
        "operation": "update",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "calendar_id": "primary",
        "event_id": "calendar-event-id-from-create",
        "summary": "Calendar write test updated",
        "start": "2026-03-22T10:00:00",
        "end": "2026-03-22T10:30:00",
        "time_zone": "America/New_York",
    },
    {
        "integration_kind": "google",
        "resource_kind": "calendar",
        "action_kind": "delete",
        "operation": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "calendar_id": "primary",
        "event_id": "calendar-event-id-to-delete",
        "send_updates": "none",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "trash",
        "query": "subject:(\"Airbnb\")",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "subject:(\"Airbnb\")",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "trash",
        "query": "from:info@airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "from:info@airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "trash",
        "query": "from:airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "from:airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "trash",
        "query": "subject:(\"Airbnb\")",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "subject:(\"Airbnb\")",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "trash",
        "query": "from:info@airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "from:info@airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "trash",
        "query": "from:airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "delete",
        "delete_mode": "delete",
        "query": "from:airbnb.com",
    },
    {
        "integration_kind": "google",
        "resource_kind": "gmail",
        "action_kind": "delete",
        "account_scope": "all",
        "operation": "trash",
        "query": "from:airbnb.com OR from:us.travelzoo.com OR from:e.petco.com",
    },
]


GOOGLE_BRIDGE_TOOL_RESPONSE_FIELDS = {
    "ok": "True when the bridge completed successfully.",
    "integration_kind": "Echoes google for this bridge.",
    "resource_kind": "The Google resource that was read or written, such as gmail or calendar.",
    "action_kind": "The action kind, such as read, draft, send, create, update, or delete.",
    "operation": "The executed operation, such as list, read, create, update, send, trash, or delete.",
    "summary_text": "Human-readable summary of the bridge result.",
    "result": "Structured JSON payload returned by the Google bridge.",
    "steps": "Ordered step results when a multi-step payload was supplied.",
    "accounts": "Normalized connected account metadata used for the run.",
    "error": "Error text when the bridge could not complete.",
}
