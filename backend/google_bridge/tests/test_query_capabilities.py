from google_bridge.services.query_capabilities import get_google_query_capabilities


def test_google_query_capabilities_document_surface_support():
    gmail = get_google_query_capabilities(resource_kind="gmail", action_kind="read", operation="list")
    calendar = get_google_query_capabilities(resource_kind="calendar", action_kind="read", operation="list")

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
