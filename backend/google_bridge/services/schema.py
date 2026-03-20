from __future__ import annotations

from copy import deepcopy

GOOGLE_BRIDGE_TOOL_NAME = "google_bridge"
GOOGLE_BRIDGE_TOOL_GROUP_NAME = "Google Bridge"
GOOGLE_BRIDGE_TOOL_DESCRIPTION = (
    "Read and write Google Gmail and Calendar data through a JSON bridge, with Gmail draft/send workflows supported. "
    "Use this tool directly for live reads and Gmail draft/send/trash/delete workflows; the same payload shape is reused by scheduled headless runs. "
    "Gmail draft, send, trash, and delete workflows are supported. Preferred Gmail write flow is to create a draft first, then send that draft when ready. Preferred Gmail deletion flow is to trash first unless permanent deletion is explicitly intended. Calendar create, update, and delete workflows are supported. Use local time for Calendar inputs, and keep the same time zone convention across create/update calls. "
    "For Gmail, list operations return message IDs and thread IDs; use a follow-up google_bridge read operation with message_id to fetch subject, sender, snippet, and metadata for each message. "
    "Use the account_email or google_subject returned by the list step when you do the follow-up read. For trash/delete, do not use read as a lookup step. Use the latest list result's message_id directly when possible; if you only have a query, the query must uniquely identify exactly one message and should go directly to the trash/delete operation. "
    "Calendar queries are local-time oriented: use the user's local time first, not GMT or Zulu, unless you convert from local time before querying. "
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
                "description": "Current supported values: list, read, create, update, send, trash, delete. Use create for Gmail drafts or Calendar creates, update for Calendar edits, send for Gmail delivery, trash to move a Gmail message to trash, and delete for permanent Gmail or Calendar deletion. For Gmail writes, create the draft first, then send that draft by supplying draft_id.",
            },
            "account_scope": {
                "type": "string",
                "enum": ["primary", "all"],
                "default": "primary",
                "description": "Primary uses the best matching connected account. All merges across every connected account in the workspace.",
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
                "description": "Gmail search query string. Use Gmail list to collect message IDs, then use message_id with read to inspect each message. For trash/delete, do not use read as a lookup step. Use the query directly only when it uniquely matches exactly one message and route it to trash/delete.",
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
                "description": "Planned Gmail mutation field. Use trash for the safe default and delete for permanent deletion. When present, the bridge uses this field to choose the Gmail delete action.",
            },
            "label_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional Gmail label id filters.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "Maximum rows to return for Gmail list or Calendar list. For Gmail, list returns message IDs only; fetch message details with read and message_id.",
            },
            "message_id": {
                "type": "string",
                "description": "Optional Gmail message identifier for read, trash, or delete operations. Use this after a Gmail list call to fetch subject, from, date, snippet, and metadata for one message. Pair it with the account_email or google_subject returned by the list step. For trash/delete, use the latest list result's message_id directly whenever possible; only fall back to a unique query when it identifies exactly one message.",
            },
            "calendar_id": {
                "type": "string",
                "description": "Optional calendar identifier. Calendar list queries use local-time bounds. Calendar create/update/delete writes also target this calendar. Defaults to primary.",
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
                "description": "Calendar mutation field. Event start in local time unless an explicit offset is supplied. Use the user's local time zone when one is known; otherwise assume Eastern Time (EST/EDT as appropriate for daylight savings).",
            },
            "end": {
                "type": "string",
                "description": "Calendar mutation field. Event end in local time unless an explicit offset is supplied. Use the user's local time zone when one is known; otherwise assume Eastern Time (EST/EDT as appropriate for daylight savings).",
            },
            "time_zone": {
                "type": "string",
                "description": "Calendar mutation field. IANA time zone name for calendar writes. Use the user's local time zone; if unknown, default to America/New_York.",
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
                "description": "Optional calendar lower bound timestamp in local time. Do not use GMT or Zulu unless you first convert to local time. If local time is unknown, assume Eastern Time (EST/EDT as appropriate for daylight savings).",
            },
            "time_max": {
                "type": "string",
                "description": "Optional calendar upper bound timestamp in local time. Do not use GMT or Zulu unless you first convert to local time. If local time is unknown, assume Eastern Time (EST/EDT as appropriate for daylight savings).",
            },
            "event_id": {
                "type": "string",
                "description": "Optional Calendar event identifier for read, update, or delete operations. Pair it with the account_email or google_subject returned by the list step.",
            },
        },
        "required": ["integration_kind", "resource_kind", "action_kind", "operation"],
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
        "steps": [
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
                "operation": "read",
                "account_scope": "primary",
                "email": "dev.agent.maestro@gmail.com",
                "message_id": "gmail-message-id-from-list",
            },
        ],
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
        "calendar_id": "primary",
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
        "operation": "trash",
        "account_scope": "primary",
        "email": "dev.agent.maestro@gmail.com",
        "message_id": "gmail-message-id-to-trash",
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
