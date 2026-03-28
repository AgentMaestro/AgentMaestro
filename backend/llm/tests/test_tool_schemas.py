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
    disable_tool = next(
        tool for tool in get_tool_schemas() if tool["name"] == "disable_scheduled_task"
    )
    enable_tool = next(
        tool for tool in get_tool_schemas() if tool["name"] == "enable_scheduled_task"
    )

    edit_description = edit_tool["parameters"]["description"]
    disable_description = disable_tool["parameters"]["description"]
    enable_description = enable_tool["parameters"]["description"]

    assert "scheduled_task_id" in edit_description
    assert "soft-delete" in disable_description
    assert "recomputes its next run time" in enable_description
    schedule_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "schedule_task")
    assert (
        "list_scheduled_tasks already returns scheduled_task_id"
        in schedule_tool["parameters"]["description"]
    )


def test_get_current_datetime_schema_advertises_tango_timezone():
    tool = next(tool for tool in get_tool_schemas() if tool["name"] == "get_current_datetime")

    description = tool["parameters"]["description"]

    assert "Tango timezone" in description
    assert "takes no arguments" in description
    assert "ISO 8601" in description
    assert "datetime" in description


def test_code_navigation_schemas_advertise_navigation_workflow():
    tool_names = {tool["name"] for tool in get_tool_schemas()}

    for tool_name in {
        "search_files",
        "list_symbols",
        "find_symbol",
        "find_references",
        "jump_to_symbol",
    }:
        assert tool_name in tool_names

    search_files = next(tool for tool in get_tool_schemas() if tool["name"] == "search_files")
    list_symbols = next(tool for tool in get_tool_schemas() if tool["name"] == "list_symbols")
    find_symbol = next(tool for tool in get_tool_schemas() if tool["name"] == "find_symbol")
    find_references = next(tool for tool in get_tool_schemas() if tool["name"] == "find_references")

    assert "does not search file contents" in search_files["parameters"]["description"]
    assert "Use this when" in search_files["parameters"]["description"]
    assert "Do not use it for content search" in search_files["parameters"]["description"]
    assert "Search one path/name query at a time" in search_files["parameters"]["description"]
    assert "scope may point to a file" in search_files["parameters"]["description"].lower()
    assert (
        "hidden files and directories are included"
        in search_files["parameters"]["description"].lower()
    )
    assert "test paths are included by default" in search_files["parameters"]["description"].lower()
    assert "symbols found in a single file" in list_symbols["parameters"]["description"]
    assert "exact or fuzzy" in find_symbol["parameters"]["description"]
    assert "impact analysis" in find_references["parameters"]["description"]
    assert "short line-numbered excerpt" in find_references["response_fields"]["items"].lower()
    assert "first hit" in find_references["parameters"]["description"].lower()
    assert "symbol_name" in find_symbol["parameters"]["required"]
    assert "context_lines" in find_references["parameters"]["properties"]
    assert "compact" in search_files["parameters"]["properties"]
    assert "compact=true" in search_files["parameters"]["description"]
    assert "scope" in search_files["parameters"]["properties"]
    assert "compact" in list_symbols["parameters"]["properties"]
    assert "scope" in list_symbols["parameters"]["properties"]
    assert "compact" in find_symbol["parameters"]["properties"]
    assert "scope" in find_symbol["parameters"]["properties"]
    assert "compact" in find_references["parameters"]["properties"]
    assert "scope" in find_references["parameters"]["properties"]
    assert (
        "short line-numbered excerpt"
        in next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "response_fields"
        ]["items"].lower()
    )
    assert (
        "short line-numbered excerpt"
        in next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "response_fields"
        ]["selection_excerpt"].lower()
    )
    assert (
        "scope"
        in next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "parameters"
        ]["properties"]
    )
    for response_fields in (
        search_files["response_fields"],
        list_symbols["response_fields"],
        find_symbol["response_fields"],
        find_references["response_fields"],
        next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "response_fields"
        ],
    ):
        assert "tool" in response_fields
        assert "compact" in response_fields
        assert "query" in response_fields
        assert "scope" in response_fields
        assert "items" in response_fields
        assert "returned_count" in response_fields
        assert "max_results_used" in response_fields
        assert "selection" in response_fields
        assert "selection_excerpt" in response_fields
        assert "stats" in response_fields
        assert "truncated" in response_fields
    assert "container/scope" in list_symbols["response_fields"]["items"]
    assert "signature" in find_symbol["response_fields"]["items"]
    assert "container/scope" in find_symbol["response_fields"]["selection"]
    assert (
        "signature"
        in next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "response_fields"
        ]["items"]
    )
    assert (
        "container/scope"
        in next(tool for tool in get_tool_schemas() if tool["name"] == "jump_to_symbol")[
            "response_fields"
        ]["selection"]
    )


def test_google_bridge_schema_advertises_mutation_aware_payload_contract():
    google_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "google_bridge")

    tool_description = google_tool["description"]
    description = google_tool["parameters"]["description"]
    query_description = google_tool["parameters"]["properties"]["query"]["description"]
    resource_kind = google_tool["parameters"]["properties"]["resource_kind"]["description"]
    action_kind = google_tool["parameters"]["properties"]["action_kind"]["description"]
    calendar_time_zone = google_tool["parameters"]["properties"]["time_zone"]["description"]

    assert "Gmail draft/send workflows" in tool_description
    assert "Drive, Docs, and Sheets" in tool_description
    assert "REQUIRED PARAMETERS" in description
    assert "WORKING EXAMPLE PAYLOADS" in description
    assert "steps" in description
    assert "create a draft first" in description
    assert "Calendar create, update, and delete workflows are supported" in description
    assert "Drive, Docs, and Sheets use read/export workflows" in action_kind
    assert "trash" in description
    assert "gmail" in resource_kind
    assert "calendar" in resource_kind
    assert "drive" in resource_kind
    assert "docs" in resource_kind
    assert "sheets" in resource_kind
    assert "Draft and send are supported for Gmail writes" in action_kind
    assert "draft first" in action_kind
    assert "trash/delete workflows" in action_kind
    assert "Create, update, and delete are supported for Calendar writes" in action_kind
    assert "America/New_York" in calendar_time_zone
    assert "generic google_bridge query language" in query_description.lower()
    assert "and, or, not" in query_description.lower()
    assert "grouped alternation is allowed inside fielded clauses" in query_description.lower()
    assert "supported query fields vary by surface" in query_description.lower()
    assert "drive list/read" in query_description.lower()
    assert "docs and sheets reads use direct file identifiers" in query_description.lower()


def test_lint_and_format_schema_advertise_cmd_only_for_command():
    lint_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "lint_runner")
    format_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "format_runner")

    lint_description = lint_tool["parameters"]["description"]
    format_description = format_tool["parameters"]["description"]
    lint_cmd_description = lint_tool["parameters"]["properties"]["cmd"]["description"]
    format_cmd_description = format_tool["parameters"]["properties"]["cmd"]["description"]

    assert "omit `cmd` entirely" in lint_description
    assert "omit `cmd` entirely" in format_description
    assert "Use `cmd` only when `tool=command`" in lint_description
    assert "Use `cmd` only when `tool=command`" in format_description
    assert "Only valid when tool=command" in lint_cmd_description
    assert "Only valid when tool=command" in format_cmd_description
    for response_fields in (lint_tool["response_fields"], format_tool["response_fields"]):
        assert "requested_cwd" in response_fields
        assert "resolved_cwd" in response_fields
        assert "requested_paths" in response_fields
        assert "resolved_paths" in response_fields
        assert "command" in response_fields

    run_command_safe = next(tool for tool in get_tool_schemas() if tool["name"] == "run_command_safe")
    assert "requested_cwd" in run_command_safe["response_fields"]
    assert "resolved_cwd" in run_command_safe["response_fields"]


def test_path_aware_tool_schemas_advertise_requested_and_resolved_fields():
    tool_lookup = {tool["name"]: tool for tool in get_tool_schemas()}

    file_patch_fields = tool_lookup["file_patch"]["response_fields"]
    assert "requested_path" in file_patch_fields
    assert "resolved_path" in file_patch_fields
    assert "requested_repo_dir" in file_patch_fields
    assert "resolved_repo_dir" in file_patch_fields

    for tool_name in {
        "git_add",
        "git_apply",
        "git_branch_create",
        "git_checkout",
        "git_commit",
        "git_diff",
        "git_log",
        "git_push",
    }:
        response_fields = tool_lookup[tool_name]["response_fields"]
        assert "requested_repo_dir" in response_fields
        assert "resolved_repo_dir" in response_fields

    assert "requested_paths" in tool_lookup["git_add"]["response_fields"]
    assert "resolved_paths" in tool_lookup["git_add"]["response_fields"]
    assert "requested_paths" in tool_lookup["git_commit"]["response_fields"]
    assert "resolved_paths" in tool_lookup["git_commit"]["response_fields"]
    assert "requested_paths" in tool_lookup["git_diff"]["response_fields"]
    assert "resolved_paths" in tool_lookup["git_diff"]["response_fields"]


def test_coverage_runner_schema_advertises_file_summaries():
    coverage_tool = next(tool for tool in get_tool_schemas() if tool["name"] == "coverage_runner")

    assert "coverage summaries" in coverage_tool["response_fields"]["files"]
