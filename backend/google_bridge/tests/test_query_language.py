import pytest

from google_bridge.services.query_language import (
    QueryAnd,
    QueryEmpty,
    QueryField,
    QueryLanguageError,
    QueryNot,
    QueryOr,
    QueryTerm,
    parse_query,
)


def test_parse_query_supports_grouped_or_in_field_clause():
    node = parse_query("from:(dsmith@aol.com OR dsmyth@aol.com)")

    assert node == QueryField(
        name="from",
        value=QueryOr(
            (
                QueryTerm("dsmith@aol.com"),
                QueryTerm("dsmyth@aol.com"),
            )
        ),
    )
    assert node.to_dict() == {
        "type": "field",
        "name": "from",
        "value": {
            "type": "or",
            "items": [
                {"type": "term", "value": "dsmith@aol.com"},
                {"type": "term", "value": "dsmyth@aol.com"},
            ],
        },
    }


def test_parse_query_supports_top_level_boolean_composition():
    node = parse_query('from:(dsmith@aol.com or dsmyth@aol.com) AND subject:"Airbnb Receipt"')

    assert node == QueryAnd(
        (
            QueryField(
                name="from",
                value=QueryOr(
                    (
                        QueryTerm("dsmith@aol.com"),
                        QueryTerm("dsmyth@aol.com"),
                    )
                ),
            ),
            QueryField(name="subject", value=QueryTerm("Airbnb Receipt")),
        )
    )
    assert node.to_dict()["type"] == "and"


def test_parse_query_supports_not_and_implicit_and():
    node = parse_query("NOT (label_ids:promotions inbox newer_than:1d)")

    assert node == QueryNot(
        QueryAnd(
            (
                QueryField(name="label_ids", value=QueryTerm("promotions")),
                QueryTerm("inbox"),
                QueryField(name="newer_than", value=QueryTerm("1d")),
            )
        )
    )
    assert node.to_dict() == {
        "type": "not",
        "item": {
            "type": "and",
            "items": [
                {
                    "type": "field",
                    "name": "label_ids",
                    "value": {"type": "term", "value": "promotions"},
                },
                {"type": "term", "value": "inbox"},
                {
                    "type": "field",
                    "name": "newer_than",
                    "value": {"type": "term", "value": "1d"},
                },
            ],
        },
    }


def test_parse_query_returns_empty_node_for_blank_input():
    assert parse_query("   ") == QueryEmpty()


def test_parse_query_rejects_unmatched_parenthesis():
    with pytest.raises(QueryLanguageError, match="unmatched opening parenthesis"):
        parse_query("from:(a OR b")
