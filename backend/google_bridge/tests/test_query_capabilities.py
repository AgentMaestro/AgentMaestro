from google_bridge.services.query_capabilities import get_google_query_capabilities


def test_google_query_capabilities_document_surface_support():
    gmail = get_google_query_capabilities(resource_kind="gmail", action_kind="read", operation="list")
    calendar = get_google_query_capabilities(resource_kind="calendar", action_kind="read", operation="list")
    drive = get_google_query_capabilities(resource_kind="drive", action_kind="read", operation="list")
    docs = get_google_query_capabilities(resource_kind="docs", action_kind="read", operation="read")
    sheets = get_google_query_capabilities(resource_kind="sheets", action_kind="read", operation="read")
    people_list = get_google_query_capabilities(resource_kind="people", action_kind="read", operation="list")
    people_search = get_google_query_capabilities(resource_kind="people", action_kind="read", operation="search")
    people_read = get_google_query_capabilities(resource_kind="people", action_kind="read", operation="read")

    assert gmail.query_enabled is True
    assert gmail.resource_kind == "gmail"
    assert {"from", "to", "subject", "label_ids", "in", "is", "newer_than", "older_than"}.issubset(
        gmail.supported_fields
    )
    assert gmail.supported_operators == frozenset({"AND", "OR", "NOT"})
    assert gmail.supports_parentheses is True

    assert calendar.query_enabled is True
    assert calendar.resource_kind == "calendar"
    assert calendar.supported_fields == frozenset({"q"})
    assert calendar.supported_operators == frozenset({"AND", "OR"})
    assert calendar.supports_parentheses is True

    assert drive.query_enabled is True
    assert drive.resource_kind == "drive"
    assert {"q", "name", "mime_type", "modified_time", "created_time"}.issubset(drive.supported_fields)
    assert drive.supported_operators == frozenset({"AND", "OR", "NOT"})
    assert drive.supports_parentheses is True

    assert docs.query_enabled is False
    assert docs.resource_kind == "docs"
    assert docs.supported_fields == frozenset()
    assert docs.supported_operators == frozenset()
    assert docs.supports_parentheses is False

    assert sheets.query_enabled is False
    assert sheets.resource_kind == "sheets"
    assert sheets.supported_fields == frozenset()
    assert sheets.supported_operators == frozenset()
    assert sheets.supports_parentheses is False

    assert people_list.query_enabled is False
    assert people_list.resource_kind == "people"
    assert people_list.action_kinds == frozenset({"read", "create", "update", "delete"})
    assert {"person_fields", "read_mask", "resource_name", "page_size", "page_token", "sort_order", "request_sync_token", "sync_token", "sources"}.issubset(
        people_list.supported_fields
    )
    assert people_list.supported_operators == frozenset()
    assert people_list.supports_parentheses is False

    assert people_search.query_enabled is False
    assert people_search.resource_kind == "people"
    assert people_search.supported_fields == people_list.supported_fields
    assert people_search.supported_operators == frozenset()
    assert people_search.supports_parentheses is False

    assert people_read.query_enabled is False
    assert people_read.resource_kind == "people"
    assert people_read.supported_fields == people_list.supported_fields
    assert people_read.supported_operators == frozenset()
    assert people_read.supports_parentheses is False
