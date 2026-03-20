from llm.services.tool_schemas import get_tool_schemas


def test_schedule_task_schema_advertises_generic_headless_scheduling():
    schedule_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "schedule_task")

    task_type_enum = schedule_tool["parameters"]["properties"]["task_type"]["enum"]
    description = schedule_tool["parameters"]["description"]

    assert "other_task" in task_type_enum
    assert len(task_type_enum) == 1
    assert "headless_run" in description
    assert "backup failover applies automatically" in description


def test_scheduled_task_management_schema_advertises_edit_disable_enable():
    tool_names = {tool["name"] for tool in get_tool_schemas()}

    assert "edit_scheduled_task" in tool_names
    assert "disable_scheduled_task" in tool_names
    assert "enable_scheduled_task" in tool_names

    edit_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "edit_scheduled_task")
    disable_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "disable_scheduled_task")
    enable_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "enable_scheduled_task")

    edit_description = edit_tool["parameters"]["description"]
    disable_description = disable_tool["parameters"]["description"]
    enable_description = enable_tool["parameters"]["description"]

    assert "scheduled_task_id" in edit_description
    assert "soft-delete" in disable_description
    assert "recomputes its next run time" in enable_description
    schedule_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "schedule_task")
    assert "list_scheduled_tasks already returns scheduled_task_id" in schedule_tool["parameters"]["description"]


def test_google_bridge_schema_advertises_mutation_aware_payload_contract():
    google_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "google_bridge")

    tool_description = google_tool["description"]
    description = google_tool["parameters"]["description"]
    resource_kind = google_tool["parameters"]["properties"]["resource_kind"]["description"]
    action_kind = google_tool["parameters"]["properties"]["action_kind"]["description"]
    calendar_time_zone = google_tool["parameters"]["properties"]["time_zone"]["description"]

    assert "Gmail draft/send workflows" in tool_description
    assert "REQUIRED PARAMETERS" in description
    assert "WORKING EXAMPLE PAYLOADS" in description
    assert "steps" in description
    assert "create a draft first" in description
    assert "Calendar create, update, and delete workflows are supported" in description
    assert "trash" in description
    assert "gmail" in resource_kind
    assert "calendar" in resource_kind
    assert "Draft and send are supported for Gmail writes" in action_kind
    assert "draft first" in action_kind
    assert "trash/delete workflows" in action_kind
    assert "Create, update, and delete are supported for Calendar writes" in action_kind
    assert "America/New_York" in calendar_time_zone
