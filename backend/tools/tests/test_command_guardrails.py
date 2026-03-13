from tools.services.command_guardrails import classify_run_command_alias


def test_classify_run_command_alias_recommends_git_tool():
    recommendation = classify_run_command_alias({"cmd": ["git", "add", "README.md"], "cwd": "."})

    assert recommendation is not None
    assert recommendation.tool_name == "git_add"


def test_classify_run_command_alias_rejects_cmd_shell_chained_git_tool():
    recommendation = classify_run_command_alias(
        {
            "cmd": ["cmd", "/C", "cd", r"smoke\git-wave2\repo", "&&", "git", "commit", "-m", "msg"],
            "cwd": ".",
        }
    )

    assert recommendation is not None
    assert recommendation.tool_name == "git_commit"


def test_classify_run_command_alias_rejects_git_branch_command():
    recommendation = classify_run_command_alias(
        {"cmd": ["cmd", "/C", "cd", r"smoke\git-wave2\repo", "&&", "git", "branch", "-M", "main"], "cwd": "."}
    )

    assert recommendation is not None
    assert recommendation.tool_name == "git_branch_create"


def test_classify_run_command_alias_recommends_file_read_for_powershell_get_content():
    recommendation = classify_run_command_alias(
        {"cmd": ["powershell", "-Command", "Get-Content backend/README.md"], "cwd": "."}
    )

    assert recommendation is not None
    assert recommendation.tool_name == "file_read"


def test_classify_run_command_alias_allows_non_alias_command():
    recommendation = classify_run_command_alias({"cmd": ["python", "--version"], "cwd": "."})

    assert recommendation is None
