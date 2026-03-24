from google_bridge.services.query_planner import plan_google_query


def test_plan_google_query_expands_grouped_or_and_not_into_multiple_calls():
    plan = plan_google_query(
        "from:(dsmith@aol.com OR dsmyth@aol.com) AND NOT label_ids:promotions",
        resource_kind="gmail",
        action_kind="read",
        operation="list",
    )

    assert plan.execution_mode == "fanout"
    assert len(plan.clauses) == 2
    assert plan.query_strings == (
        "from:dsmith@aol.com -label_ids:promotions",
        "from:dsmyth@aol.com -label_ids:promotions",
    )
    assert "from" in plan.to_dict()["capabilities"]["supported_fields"]
    assert plan.to_dict()["normalized_query"] == (
        "from:dsmith@aol.com -label_ids:promotions OR from:dsmyth@aol.com -label_ids:promotions"
    )
    assert plan.to_dict()["calls"][0]["literals"][0]["field_name"] == "from"


def test_plan_google_query_preserves_single_clause_queries():
    plan = plan_google_query(
        "from:airbnb.com",
        resource_kind="gmail",
        action_kind="read",
        operation="list",
    )

    assert plan.execution_mode == "single"
    assert plan.query_strings == ("from:airbnb.com",)
    assert plan.to_dict()["calls"][0]["query"] == "from:airbnb.com"


def test_plan_google_query_expands_calendar_grouped_or_into_multiple_q_calls():
    plan = plan_google_query(
        "q:(team sync OR planning)",
        resource_kind="calendar",
        action_kind="read",
        operation="list",
    )

    assert plan.execution_mode == "fanout"
    assert plan.query_strings == ("team sync", "planning")
    assert plan.to_dict()["capabilities"]["resource_kind"] == "calendar"
    assert plan.to_dict()["calls"][0]["query"] == "team sync"
