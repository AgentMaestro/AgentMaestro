from llm.services.tool_schemas import get_tool_schemas


def test_schedule_task_schema_advertises_generic_headless_scheduling():
    schedule_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "schedule_task")

    task_type_enum = schedule_tool["parameters"]["properties"]["task_type"]["enum"]
    description = schedule_tool["parameters"]["description"]

    assert "other_task" in task_type_enum
    assert "other_daily_task" in task_type_enum
    assert "headless_run" in description
    assert "backups" in description
