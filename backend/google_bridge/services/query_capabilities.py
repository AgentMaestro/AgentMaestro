from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True, slots=True)
class GoogleQueryCapabilities:
    resource_kind: str
    action_kinds: frozenset[str]
    query_enabled: bool
    supported_fields: frozenset[str]
    supported_operators: frozenset[str] = frozenset({"AND", "OR", "NOT"})
    supports_parentheses: bool = True
    clause_limit_setting: str = "GOOGLE_BRIDGE_QUERY_CLAUSE_LIMIT"
    default_clause_limit: int = 10

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_kind": self.resource_kind,
            "action_kinds": sorted(self.action_kinds),
            "query_enabled": self.query_enabled,
            "supported_fields": sorted(self.supported_fields),
            "supported_operators": sorted(self.supported_operators),
            "supports_parentheses": self.supports_parentheses,
            "clause_limit_setting": self.clause_limit_setting,
            "default_clause_limit": self.default_clause_limit,
        }


_GMAIL_QUERY_FIELDS = frozenset(
    {
        "from",
        "to",
        "subject",
        "label_ids",
        "in",
        "is",
        "newer_than",
        "older_than",
    }
)

_CALENDAR_QUERY_FIELDS = frozenset(
    {
        "calendar_id",
        "q",
        "time_min",
        "time_max",
        "updated_min",
    }
)

_DRIVE_QUERY_FIELDS = frozenset(
    {
        "q",
        "name",
        "mime_type",
        "modified_time",
        "created_time",
        "trashed",
    }
)


def get_google_query_capabilities(*, resource_kind: str, action_kind: str, operation: str) -> GoogleQueryCapabilities:
    resource = str(resource_kind or "").strip().lower()
    action = str(action_kind or "").strip().lower()
    operation_name = str(operation or "").strip().lower()

    if resource == "gmail":
        query_enabled = action in {"read", "delete"} and operation_name in {"list", "read", "trash", "delete"}
        return GoogleQueryCapabilities(
            resource_kind="gmail",
            action_kinds=frozenset({"read", "delete"}),
            query_enabled=query_enabled,
            supported_fields=_GMAIL_QUERY_FIELDS,
            supported_operators=frozenset({"AND", "OR", "NOT"}),
        )

    if resource == "calendar":
        return GoogleQueryCapabilities(
            resource_kind="calendar",
            action_kinds=frozenset({"read"}),
            query_enabled=action == "read" and operation_name in {"list", "read"},
            supported_fields=frozenset({"q"}),
            supported_operators=frozenset({"AND", "OR"}),
        )

    if resource == "drive":
        return GoogleQueryCapabilities(
            resource_kind="drive",
            action_kinds=frozenset({"read"}),
            query_enabled=action == "read" and operation_name in {"list", "read"},
            supported_fields=_DRIVE_QUERY_FIELDS,
            supported_operators=frozenset({"AND", "OR", "NOT"}),
        )

    if resource in {"docs", "sheets"}:
        return GoogleQueryCapabilities(
            resource_kind=resource,
            action_kinds=frozenset({"read", "export"}),
            query_enabled=False,
            supported_fields=frozenset(),
            supported_operators=frozenset({"AND", "OR", "NOT"}),
        )

    return GoogleQueryCapabilities(
        resource_kind=resource or "unknown",
        action_kinds=frozenset(),
        query_enabled=False,
        supported_fields=frozenset(),
    )


def get_query_clause_limit() -> int:
    for setting_name in ("GOOGLE_BRIDGE_QUERY_CLAUSE_LIMIT", "GMAIL_OR_CLAUSE_LIMIT"):
        try:
            value = getattr(settings, setting_name, None)
            if value is not None:
                return max(1, int(value or 10))
        except Exception:
            continue
    return 10
