import json
from copy import deepcopy

from google_bridge.services.schema import (
    GOOGLE_BRIDGE_TOOL_DESCRIPTION,
    GOOGLE_BRIDGE_TOOL_GROUP_NAME,
    GOOGLE_BRIDGE_TOOL_NAME,
    build_google_bridge_args_schema,
)

from .models import ToolRisk


def _schema_docs(required_parameters, examples, response_fields=None) -> str:
    lines: list[str] = []
    required = list(required_parameters or [])
    if required:
        lines.append("REQUIRED PARAMETERS:")
        for name in required:
            lines.append(f"- {name}")
    else:
        lines.append("REQUIRED PARAMETERS:")
        lines.append("- none")
    example_items = []
    if isinstance(examples, list):
        example_items = [item for item in examples if item is not None]
    elif examples is not None:
        example_items = [examples]
    if example_items:
        lines.append("")
        if len(example_items) == 1:
            lines.append("MINIMAL WORKING EXAMPLE PAYLOAD:")
            lines.append(json.dumps(example_items[0], indent=2))
        else:
            lines.append("WORKING EXAMPLE PAYLOADS:")
            for example in example_items:
                lines.append(json.dumps(example, indent=2))
    if response_fields:
        lines.append("")
        lines.append("RESPONSE FIELDS TO EXPECT:")
        for field_name, meaning in response_fields.items():
            lines.append(f"- {field_name}: {meaning}")
    return "\n".join(lines)


_TOOL_EXAMPLES = {
    "file_read": [
        {"path": "README.md", "mode": "text"},
        {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py", "mode": "text"},
    ],
    "repo_tree": [
        {"path": "backend", "max_depth": 3, "include_files": True},
        {
            "path": "C:\\Dev\\AgentMaestro\\backend\\runs\\tests\\fixtures\\tool_repo",
            "max_depth": 3,
            "include_files": True,
        },
    ],
    "file_write": [
        {"path": "notes/hello.py", "content": "print('hello')\n"},
        {
            "path": "C:/tmp/agentmaestro/smoke_tools/hello.py",
            "absolute_root": "C:\\tmp\\agentmaestro\\smoke_tools",
            "content": "print('hello')\n",
            "overwrite": True,
        },
    ],
    "file_delete": [
        {"path": "notes/hello.py"},
        {
            "path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py",
            "absolute_root": "C:\\tmp\\agentmaestro\\smoke_tools",
        },
    ],
    "file_patch": [
        {
            "path": "notes/hello.py",
            "patch_unified": "--- a/notes/hello.py\n+++ b/notes/hello.py\n@@ -1,1 +1,1 @@\n-print('hello')\n+print('hello world')\n",
        },
        {
            "path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py",
            "absolute_root": "C:\\tmp\\agentmaestro\\smoke_tools",
            "patch_unified": "--- a/hello.py\n+++ b/hello.py\n@@ -1,1 +1,1 @@\n-print('hello')\n+print('hello world')\n",
        },
    ],
    "git_add": [
        {"repo_dir": ".", "paths": ["backend/tools/admin.py"]},
        {
            "repo_dir": "C:\\Dev\\AgentMaestro",
            "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"],
        },
    ],
    "git_status": [
        {"repo_dir": ".", "porcelain": "v2", "include_untracked": True},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "porcelain": "v2", "include_untracked": True},
    ],
    "git_diff": [
        {"repo_dir": ".", "paths": ["backend/tools/admin.py"], "staged": False},
        {
            "repo_dir": "C:\\Dev\\AgentMaestro",
            "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"],
            "staged": False,
        },
    ],
    "git_log": [
        {"repo_dir": ".", "max_count": 5},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "max_count": 5},
    ],
    "git_apply": [
        {
            "repo_dir": ".",
            "patch_unified": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
        },
        {
            "repo_dir": "C:\\Dev\\AgentMaestro",
            "patch_unified": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
        },
    ],
    "git_branch_create": [
        {"repo_dir": ".", "name": "smoke/tool-docs", "checkout": False},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "name": "smoke/tool-docs", "checkout": False},
    ],
    "git_checkout": [
        {"repo_dir": ".", "ref": "main"},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "ref": "main"},
    ],
    "git_commit": [
        {"repo_dir": ".", "message": "Smoke test commit"},
        {
            "repo_dir": "C:\\Dev\\AgentMaestro",
            "message": "Smoke test commit",
            "paths_to_add": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"],
        },
    ],
    "git_push": [
        {"repo_dir": ".", "remote": "origin", "ref": "main"},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "remote": "origin", "ref": "main"},
    ],
    "python_exec": [
        {"code": "from pathlib import Path\nprint(Path('README.md').exists())"},
        {
            "files": [{"path": "scripts/hello.py", "content_b64": "cHJpbnQoJ2hlbGxvJykK"}],
            "entrypoint": "scripts/hello.py",
        },
    ],
    "webhook": {"url": "https://example.test/webhook", "payload": {"event": "smoke"}},
    "coverage_runner": [
        {
            "kind": "pytest_coverage",
            "cwd": ".",
            "args": ["toolrunner/app/tests/test_file_write.py"],
            "timeout_ms": 600000,
        },
        {
            "kind": "pytest_coverage",
            "cwd": "C:\\Dev\\AgentMaestro",
            "args": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"],
            "timeout_ms": 600000,
        },
    ],
    "format_runner": [
        {
            "tool": "ruff_format",
            "mode": "apply",
            "cwd": ".",
            "paths": ["toolrunner/app/tests/test_file_write.py"],
        },
        {
            "tool": "ruff_format",
            "mode": "apply",
            "cwd": "C:\\Dev\\AgentMaestro",
            "paths": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"],
        },
    ],
    "lint_runner": [
        {"tool": "ruff", "cwd": ".", "paths": ["backend/tools"]},
        {
            "tool": "ruff",
            "cwd": "C:\\Dev\\AgentMaestro",
            "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools"],
        },
    ],
    "run_command": {
        "cmd": [
            "cmd",
            "/C",
            "echo RUN_COMMAND_SMOKE_OK && dir C:\\Dev\\AgentMaestro\\toolrunner\\app\\tools",
        ],
        "cwd": ".",
    },
    "run_command_safe": {
        "argv": ["python", "manage.py", "check"],
        "cwd": ".",
        "timeout_seconds": 60,
    },
    "search_code": [
        {"query": "provider_call_id", "root": "backend", "include_globs": ["**/*.py"]},
        {
            "query": "provider_call_id",
            "root": "C:\\Dev\\AgentMaestro\\backend",
            "include_globs": ["**/*.py"],
        },
    ],
    "web_search": {"query": "AgentMaestro orchestration platform", "max_results": 5},
    "fetch_url": {"url": "https://example.com", "extract": "main_text", "max_chars": 4000},
    "remember": {
        "scope_type": "sandbox",
        "scope_id": "C:/Dev/AgentMaestro",
        "memory_kind": "semantic",
        "content": "The backend app holds Django state.",
        "tags": ["architecture"],
        "importance": 0.5,
        "summary": "Backend location",
        "dedupe_key": "fact:backend-location",
        "dedupe_mode": "key",
        "source_kind": "manual_remember",
        "source_ref": "operator:console",
        "pinned": True,
        "expires_at": "2026-03-31T23:59:59Z",
    },
    "search_memory": {
        "query": "backend",
        "scope_type": "sandbox",
        "scope_id": "C:/Dev/AgentMaestro",
        "memory_kind": "semantic",
        "limit": 5,
    },
    "schedule_task": [
        {
            "title": "daily repo backup summary",
            "task_type": "other_task",
            "execution_mode": "headless_run",
            "timezone": "America/New_York",
            "local_time": "05:00",
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
                "notes": "Use git status and git log, then create a concise backup commit if there are changes.",
            },
        },
        {
            "title": "weekly digest",
            "task_type": "other_task",
            "execution_mode": "headless_run",
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "daily",
                "interval": 1,
                "local_time": "05:00",
            },
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
                "notes": "Use git status and git log, then create a concise backup commit if there are changes.",
            },
        },
        {
            "title": "daily maintenance digest",
            "task_type": "other_task",
            "execution_mode": "headless_run",
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "hourly",
                "interval": 1,
                "by_weekday": ["mon", "wed", "fri", "sat"],
                "run_minute": 0,
                "window_start_time": "09:00",
                "window_end_time": "19:00",
            },
            "execution_payload": {
                "objective": "Summarize overnight changes and produce a short maintenance digest.",
                "notes": "Review logs, open issues, and any queued tasks before writing the digest.",
            },
        },
    ],
    "edit_scheduled_task": [
        {
            "scheduled_task_id": "scheduled-task-id-from-list",
            "title": "daily repo backup summary",
            "enabled": True,
            "timezone": "America/New_York",
            "local_time": "05:00",
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
            },
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "daily",
                "interval": 1,
                "local_time": "05:00",
            },
        }
    ],
    "disable_scheduled_task": [{"scheduled_task_id": "scheduled-task-id-from-list"}],
    "enable_scheduled_task": [{"scheduled_task_id": "scheduled-task-id-from-list"}],
    "list_scheduled_tasks": {"enabled_only": True, "limit": 10},
    "spawn_subrun": {
        "input_text": "Research the current weather outlook for Ocala tennis conditions and return a concise summary.",
        "metadata": {"purpose": "focused research", "topic": "weather"},
        "join_policy": "WAIT_ALL",
        "failure_policy": "IGNORE_FAILURE",
    },
    "get_current_datetime": [{}],
    "shell_exec": [
        {"cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"], "cwd": "."},
        {
            "cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"],
            "cwd": "C:\\Dev\\AgentMaestro",
        },
    ],
    "test_runner": [
        {
            "kind": "pytest",
            "pytest_args": ["toolrunner/app/tests/test_file_write.py"],
            "cwd": ".",
            "parse": "pytest",
            "timeout_ms": 600000,
        },
        {
            "kind": "pytest",
            "pytest_args": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"],
            "cwd": "C:\\Dev\\AgentMaestro",
            "parse": "pytest",
            "timeout_ms": 600000,
        },
    ],
    "run_tests": {
        "suites": ["backend"],
        "timeout_seconds": 900,
    },
    "typecheck_runner": [
        {
            "tool": "mypy",
            "cwd": ".",
            "args": ["backend"],
            "timeout_ms": 300000,
            "max_output_bytes": 262144,
        },
        {
            "tool": "mypy",
            "cwd": "C:\\Dev\\AgentMaestro",
            "args": ["C:\\Dev\\AgentMaestro\\backend"],
            "timeout_ms": 300000,
            "max_output_bytes": 262144,
        },
        {
            "tool": "command",
            "cwd": ".",
            "cmd": ["cmd", "/C", "echo", "TYPECHECK_OK"],
            "timeout_ms": 300000,
            "max_output_bytes": 262144,
        },
    ],
}

_TOOL_ADDITIONAL_DOCS = {
    "file_read": "\n\nPATH NOTES:\n"
    "- `requested_path` is the caller-supplied path value.\n"
    "- `resolved_path` is the exact filesystem path that will be read.\n"
    "- `requested_root` and `resolved_root` describe the read base when the tool resolves through a repository root.\n"
    "- The result payload uses `requested_path` and `resolved_path` only.",
    "repo_tree": "\n\nPATH NOTES:\n"
    "- `requested_root` is the caller-supplied root value.\n"
    "- `resolved_root` is the exact filesystem root that will be listed.\n"
    "- The result payload uses `requested_root` and `resolved_root` only.",
    "file_write": "\n\nPATH NOTES:\n"
    "- `requested_path` is the caller-supplied path value, normalized to forward slashes in the response.\n"
    "- `resolved_path` is the exact filesystem path that will be written.\n"
    "- `requested_root` and `resolved_root` describe the write base when the tool resolves through a repository root.\n"
    "- Use forward slashes in examples for relative paths so the response format is easier to compare across platforms.\n"
    "- The result payload uses `requested_path` and `resolved_path` only.\n\n"
    "WRITE MODE NOTES:\n"
    "- `overwrite` is optional.\n"
    "- Leave `overwrite=false` to avoid replacing an existing file.\n"
    "- Set `overwrite=true` when you intentionally want to replace an existing file instead of deleting it first.\n"
    "- Use `file_delete` only when the goal is to remove the file entirely.",
    "file_patch": "\n\nPATH NOTES:\n"
    "- `requested_path` is the caller-supplied target path.\n"
    "- `resolved_path` is the exact filesystem path that will be patched.\n"
    "- `requested_root` is `null` for file_patch.\n"
    "- `requested_repo_dir` / `resolved_repo_dir` may still appear as a path-root fallback for temp files or other non-repo targets.\n"
    "- The result payload uses `requested_path`, `resolved_path`, `requested_root`, `resolved_root`, `requested_repo_dir`, and `resolved_repo_dir`.\n\n"
    "PATCH FORMAT NOTES:\n"
    "- `path` may be absolute or repo-relative.\n"
    "- Repo-relative paths resolve from the repository root when one is provided in policy context.\n"
    "- `patch_unified` is required.\n"
    "- Provide a complete unified diff for a single target file.\n"
    "- Include `---` and `+++` file markers and at least one `@@` hunk header.\n"
    "- Hunk headers must use explicit, accurate unified diff ranges, for example `@@ -1,2 +1,2 @@` or `@@ -0,0 +1,3 @@`.\n"
    "- Shorthand headers like `@@ -1 +1 @@` are rejected. Even pure insertions must include counts.\n"
    "- Do not include Codex `*** Begin Patch` / `*** End Patch` fences; this tool expects only unified diff text.\n\n"
    "SUCCESSFUL PATCH EXAMPLES:\n"
    "- Modify an existing file:\n"
    "  --- a/hello.py\n"
    "  +++ b/hello.py\n"
    "  @@ -1,1 +1,1 @@\n"
    "  -print('hello')\n"
    "  +print('hello world')\n"
    "- Add a new file with `create_if_missing=true`:\n"
    "  --- /dev/null\n"
    "  +++ b/new_file.txt\n"
    "  @@ -0,0 +1 @@\n"
    "  +created by patch\n\n"
    "TROUBLESHOOTING:\n"
    "- If parsing fails, check that `---` / `+++` markers are present and every `@@` header uses explicit counts.\n"
    "- If the wrong file is targeted, make sure the diff path suffix matches the `path` argument.\n"
    "- If a hunk is rejected, re-read the file and rebuild the patch against the current contents.\n"
    "- Use standard `\\n` line endings in the diff text. Mixed or malformed newline style can break patch parsing.",
    "file_delete": "\n\nPATH NOTES:\n"
    "- `requested_path` is the caller-supplied path value.\n"
    "- `resolved_path` is the exact filesystem path that was targeted for deletion.\n"
    "- `requested_root` and `resolved_root` describe the delete base when the tool resolves through a repository root.\n"
    "- The result payload uses `requested_path` and `resolved_path` only.",
    "test_runner": "\n\nRUN MODE NOTES:\n"
    "- `kind` is required.\n"
    "- Supported kinds are `powershell_script`, `pytest`, and `command`.\n"
    "- Choose exactly one mode:\n"
    "  - `kind=powershell_script` requires `script_path`\n"
    "  - `kind=pytest` requires `pytest_args`\n"
    "  - `kind=command` requires `cmd`\n"
    "- Prefer `kind=pytest` for narrow smoke tests and `kind=powershell_script` for repo-standard test entrypoints.\n\n"
    "OUTPUT NOTES:\n"
    "- Pytest-mode results do not use `script_path`; smoke checks should assert `requested_args`/`resolved_args`, `summary`, `exit_code`, and `failed_tests` instead.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLES:\n"
    "- Pytest mode:\n"
    '  `{ "kind": "pytest", "pytest_args": ["toolrunner/app/tests/test_file_write.py"], "cwd": ".", "parse": "pytest" }`\n'
    "- PowerShell script mode:\n"
    '  `{ "kind": "powershell_script", "script_path": "backend/scripts/runtests.ps1", "cwd": "." }`\n\n'
    "TROUBLESHOOTING:\n"
    "- If you get an HTTP 500 or generic runner failure, retry once with the same payload to rule out a transient worker issue.\n"
    "- If it still fails, narrow the test target or switch from script mode to direct pytest mode.\n"
    "- If no detail is returned, inspect backend/toolrunner server logs for the underlying test command and stderr.\n"
    "- To find candidate pytest targets, inspect the repo's `tests/` directories and existing smoke-test examples.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `pytest` mode runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `pytest` is missing, the tool reports the resolved interpreter path and source.",
    "coverage_runner": "\n\nRUN MODE NOTES:\n"
    "- `kind` is required and currently must be `pytest_coverage`.\n"
    "- Provide pytest target arguments via `args`.\n"
    "- Coverage generates a `coverage.json` artifact in the working directory.\n\n"
    "OUTPUT NOTES:\n"
    "- `requested_cwd` and `resolved_cwd` show the execution directory chosen by the runner.\n"
    "- `requested_args` echoes the target list used for coverage collection.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    '- `{ "kind": "pytest_coverage", "cwd": ".", "args": ["toolrunner/app/tests/test_file_write.py"] }`\n\n'
    "TROUBLESHOOTING:\n"
    "- If coverage fails with a generic runner error, try the same target first with `test_runner`.\n"
    "- If the test run passes but coverage still fails, inspect logs for the follow-up `coverage json` command.\n"
    "- Use a narrow pytest target first to keep output and run time predictable.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- Coverage commands run through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `coverage` or `pytest` is missing, the tool reports the resolved interpreter path and source.",
    "format_runner": "\n\nRUN MODE NOTES:\n"
    "- `tool` is required.\n"
    "- Supported formatter modes are `ruff_format`, `black`, `prettier`, and `command`.\n"
    "- Choose `mode=check` for validation-only or `mode=apply` to write changes.\n"
    "- `cwd` and each item in `paths` may be absolute or repo-relative.\n"
    "- For Ruff-style runs (`ruff_format`), omit `cmd` entirely and use `paths` plus optional `args` only.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    '- `{ "tool": "ruff_format", "mode": "apply", "cwd": ".", "paths": ["toolrunner/app/tests/test_file_write.py"] }`\n\n'
    "TROUBLESHOOTING:\n"
    "- If formatting fails, try a single file in `paths` before expanding scope.\n"
    "- `changed_files` parsing is best for `ruff_format`; other formatter modes may only return stdout/stderr.\n"
    "- If you need an arbitrary formatter command, use `tool=command` and provide `cmd`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff_format` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.",
    "run_command_safe": "\n\nSAFE COMMAND NOTES:\n"
    "- `argv` is required and must be a list of strings.\n"
    "- Allowed executables are limited to `python`, `pytest`, `ruff`, `mypy`, `uv`, and `django-admin`.\n"
    "- A narrow `python -c` smoke form is allowed only when it is a single `print(...)` call with a string literal.\n"
    "- `git` is explicitly blocked. Use the dedicated `git_*` tools instead.\n"
    "- Shell composition, shell wrappers, package installs, migrations, dev servers, and other interactive or long-running commands are rejected.\n"
    "- `cwd` must stay inside the active workspace root.\n"
    "- Common rejections include `git status`, `pip install`, `python manage.py runserver`, and chained shell commands.\n",
    "run_tests": "\n\nREPO TEST SCRIPT NOTES:\n"
    "- `suites` is required and only accepts `backend`, `toolrunner`, or `all`.\n"
    "- This tool only runs the repo-owned PowerShell test entrypoints.\n"
    "- Git and arbitrary PowerShell execution are not supported here.\n"
    "- If a suite only exposes `test.ps1`, the tool automatically falls back to that script.\n",
    "run_command": "\n\nCOMMAND ARGUMENT NOTES:\n"
    "- `cmd` is required and must be a list of strings.\n"
    "- Pass the executable and each argument as a separate list item.\n"
    "- Do not send a single shell string unless you are explicitly invoking a shell such as `cmd /C` or `powershell -Command`.\n"
    "- `cwd` may be absolute or repo-relative.\n"
    "- `requested_cwd` and `resolved_cwd` appear in the response so callers can see the actual execution directory.\n"
    "- Use `run_command` only as a last resort when no specialized tool matches the task.\n"
    "- Do not use `run_command` for Git operations that map to `git_add`, `git_status`, `git_diff`, `git_log`, `git_apply`, `git_commit`, `git_push`, `git_checkout`, or `git_branch_create`.\n"
    "- Do not use `run_command` for direct file content reads that should go through `file_read`.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    '- `{ "cmd": ["cmd", "/C", "echo RUN_COMMAND_SMOKE_OK && dir C:\\\\Dev\\\\AgentMaestro\\\\toolrunner\\\\app\\\\tools"], "cwd": "." }`\n\n'
    "TROUBLESHOOTING:\n"
    "- If validation fails, check that `cmd` is an array and not a single string.\n"
    "- If you need shell features such as `&&`, invoke a shell explicitly through `cmd /C` or `powershell -Command`.\n"
    "- If the command times out, inspect the returned timeout source and effective timeout value.",
    "search_code": "\n\nPATH NOTES:\n"
    "- `requested_root` is the caller-supplied root value.\n"
    "- `resolved_root` is the exact filesystem root that will be searched.\n"
    "- The result payload uses `requested_root` and `resolved_root` only.\n"
    "- `include_globs` are evaluated relative to the resolved root.\n\n"
    "QUERY NOTES:\n"
    "- When `is_regex=true`, use regex syntax for alternatives. Prefer `|` between options instead of the word `OR`.\n"
    "- Example: `sprint|roadmap|milestone|next sprint|planning`.\n\n"
    "RESULT NOTES:\n"
    "- Match snippets include `line`, `col`, and `line_text` when the tool can derive them from the scanned text.",
    "web_search": "\n\nRESEARCH NOTES:\n"
    "- `web_search` returns lightweight search metadata only. Use `fetch_url` for page content.\n"
    "- Provider failures usually indicate missing credentials, timeout, or upstream HTTP errors.",
    "fetch_url": "\n\nFETCH NOTES:\n"
    "- Only public http/https URLs are allowed. Localhost, private-network, and internal addresses are rejected.\n"
    "- `content` returns extracted readable text, not raw HTML.\n"
    "- Use `max_chars` to keep follow-up reasoning compact.",
    "remember": "\n\nMEMORY NOTES:\n"
    "- `remember` stores a durable episodic, semantic, or procedural memory record in Django.\n"
    "- Prefer concise, stable facts or procedures rather than transient chatter.\n"
    "- `dedupe_key` identifies the same durable fact or procedure across repeated writes.\n"
    "- `dedupe_mode` controls whether writes dedupe by key, by exact content, or skip dedupe entirely.\n"
    "- Set `pinned=true` for memories that should survive normal cleanup pressure, such as durable preferences or important procedures.\n"
    "- Set `expires_at` to an ISO 8601 datetime when the memory is temporary and should naturally age out of active lookups.\n"
    "- Use `source_kind` and `source_ref` to record provenance, such as `manual_remember`, `scheduled_task_created`, or `scheduled_task_executed`.\n"
    "- Example semantic memory: `scope_type='sandbox'`, `scope_id='C:/Dev/AgentMaestro'`, `memory_kind='semantic'`, `dedupe_key='fact:backend-location'`, `content='Use apply_patch for manual edits.'`, `pinned=true`.\n"
    "- Example procedural memory: `scope_type='agent'`, `scope_id='<agent-id>'`, `memory_kind='procedural'`, `dedupe_key='procedure:test-runner'`, `content='When Telegram testing locally, clear the webhook and switch to polling first.'`.\n"
    "- Example episodic memory: `scope_type='user'`, `scope_id='<user-id>'`, `memory_kind='episodic'`, `dedupe_mode='none'`, `dedupe_key='scheduled-task-exec-bucket:<task-id>:daily-weather-report'`, `content='On March 13, 2026, Scott validated Telegram approvals end-to-end.'`, `expires_at='2026-03-31T23:59:59Z'`.",
    "schedule_task": "\n\nSCHEDULING NOTES:\n"
    "- `schedule_task` creates recurring headless agent work.\n"
    "- Scheduled work runs headlessly. Use `other_task` for the task label and put structured intent in `execution_payload`.\n"
    "- Scheduled runs inherit the agent's backup models and retry policy, so backup failover applies automatically.\n"
    "- Prefer `recurrence` for anything more complex than a single daily wall-clock time.\n"
    "- If a timezone argument is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC.\n"
    "- If the schedule depends on a relative date like tomorrow or next Friday and the current local date is not already known, call `get_current_datetime` first and anchor the schedule in that local time.\n"
    "- Use `title` and `execution_payload` to describe the recurring job clearly so the future headless run has enough context.\n"
    "- list_scheduled_tasks already returns scheduled_task_id, so use that identifier for future edit, disable, or enable operations.\n",
    "search_memory": "\n\nMEMORY NOTES:\n"
    "- `search_memory` performs simple text lookup over durable memory records.\n"
    "- Narrow by `scope_type`, `scope_id`, and `memory_kind` when the target scope is known.\n"
    "- Example targeted lookup: `query='apply_patch edits'`, `scope_type='sandbox'`, `scope_id='C:/Dev/AgentMaestro'`.\n"
    "- Example procedural lookup: `query='telegram polling'`, `scope_type='agent'`, `scope_id='<agent-id>'`, `memory_kind='procedural'`.\n"
    "- Example episodic lookup: `query='validated Telegram approvals'`, `scope_type='user'`, `scope_id='<user-id>'`, `memory_kind='episodic'`.",
    "spawn_subrun": "\n\nSUBRUN NOTES:\n"
    "- `spawn_subrun` creates a child `AgentRun` attached to the current run.\n"
    "- For headless parents, the child executes inline so the current planner/model round can continue with the child result.\n"
    "- Keep child prompts focused and self-contained.\n"
    "- Use `metadata` for operator-readable purpose labels, not large payloads.\n"
    "- The default `join_policy` is `wait_all` and the default `failure_policy` is `ignore_failure`.\n"
    "- Reserve `FAIL_FAST` for critical safety or security situations only.\n",
    "get_current_datetime": "\n\nTIME NOTES:\n"
    "- `get_current_datetime` takes no arguments.\n"
    "- It returns the current local datetime in the Tango timezone as an ISO 8601 string with offset.\n"
    "- The Tango timezone defaults to `America/New_York` and can be overridden with `TANGO_TIME_ZONE`.\n",
    "shell_exec": "\n\nPATH NOTES:\n"
    "- `cwd` may be absolute or repo-relative.\n"
    "- Repo-relative `cwd` values resolve from the repository root when one is provided in policy context.",
    "python_exec": "\n\nPATH NOTES:\n"
    "- Each `files[].path` and `entrypoint` value may be absolute or repo-relative.\n"
    "- Repo-relative values resolve from the repository root when one is provided in policy context.",
    "file_delete": "\n\nPATH NOTES:\n"
    "- `path` may be absolute or repo-relative.\n"
    "- Repo-relative paths resolve from the repository root when one is provided in policy context.",
    "git_add": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Each item in `paths` may be absolute or repo-relative to the selected repository.\n"
    "- Repo-relative values resolve from the repository root when one is provided in policy context.",
    "git_status": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.\n"
    "- Porcelain parsing is normalized to v2 internally so dirty-state detection stays stable.",
    "git_diff": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Each item in `paths` may be absolute or repo-relative to the selected repository.\n"
    "- Repo-relative values resolve from the repository root when one is provided in policy context.",
    "git_log": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
    "git_apply": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
    "git_branch_create": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
    "git_checkout": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
    "git_commit": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Each item in `paths_to_add` may be absolute or repo-relative to the selected repository.\n"
    "- Repo-relative values resolve from the repository root when one is provided in policy context.",
    "git_push": "\n\nPATH NOTES:\n"
    "- `repo_dir` may be absolute or repo-relative.\n"
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
    "lint_runner": "\n\nPATH NOTES:\n"
    "- `cwd` and each item in `paths` may be absolute or repo-relative.\n"
    "- Repo-relative values resolve from the repository root when one is provided in policy context.\n\n"
    "RUN MODE NOTES:\n"
    "- For Ruff-style runs (`ruff`), omit `cmd` entirely and use `paths` plus optional `args` only.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.",
    "typecheck_runner": "\n\nPATH NOTES:\n"
    "- `cwd` may be absolute or repo-relative.\n"
    "- `requested_cwd` and `resolved_cwd` appear in the response so callers can see the actual type-check execution directory.\n"
    "- Path arguments passed through `args` may also be absolute or repo-relative when the underlying type checker supports them.\n"
    "- `tool=command` is the escape hatch for custom checks; if it runs `python -m pytest ...` and pytest is missing, the tool returns a missing-runtime-dependency error for `pytest`.\n\n"
    "- For smoke checks, `tool=command` should use a harmless command such as `cmd /C echo TYPECHECK_OK`; malformed or empty `cmd` lists are rejected early.\n\n"
    "DEFAULT LIMITS:\n"
    "- `timeout_ms` defaults to `300000`.\n"
    "- `max_output_bytes` defaults to `262144`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `mypy` and `pyright` run through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If a Python-backed type checker is missing, the tool reports the resolved interpreter path and source.\n"
    "- When `tool=command` runs `python -m pytest ...` and `pytest` is not installed, the tool returns a missing-runtime-dependency error for `pytest`.",
}


_TOOL_RESPONSE_FIELDS = {
    "file_read": {
        "requested_path": "The path value supplied by the caller.",
        "resolved_path": "The exact filesystem path that was read.",
        "requested_root": "The root value supplied by the caller, if any.",
        "resolved_root": "The resolved parent/root directory used for the read.",
        "mode": "Whether the response content is text or binary.",
        "content": "Returned text content for text mode.",
        "content_base64": "Returned bytes encoded as base64 for binary mode.",
        "truncated": "True when max_bytes cut the response short.",
    },
    "repo_tree": {
        "requested_root": "The root value supplied by the caller.",
        "resolved_root": "The exact filesystem root that was listed.",
        "entries": "Sorted tree entries returned by the tool.",
        "stats": "Counts for files, dirs, exclusions, and allowed roots used by policy.",
        "truncated": "True when max_entries limited the walk.",
    },
    "search_code": {
        "query": "Echoes the query that was searched.",
        "is_regex": "Whether regex mode was enabled for the search.",
        "case_sensitive": "Whether matching was case-sensitive.",
        "matches": "Path-sorted files with match_count and snippet metadata for each matching file.",
        "requested_root": "The root value supplied by the caller.",
        "resolved_root": "The exact filesystem root that was searched.",
        "stats": "File counts, total matches, exclusions, and allowed roots used by policy.",
        "meta": "Optional policy metadata returned only when include_meta=true.",
        "truncated": "True when max_results or timeout limits stopped the scan.",
    },
    "web_search": {
        "query": "Echoes the submitted search query.",
        "results": "List of title/url/snippet search hits from the configured provider.",
    },
    "fetch_url": {
        "url": "Original requested URL.",
        "final_url": "Resolved final URL after safe redirects.",
        "title": "Best-effort page title for HTML responses.",
        "content": "Readable extracted content capped by max_chars.",
        "content_type": "HTTP content type returned by the server.",
        "status_code": "Final HTTP status code.",
        "truncated": "True when download or returned content was capped.",
    },
    "remember": {
        "memory_id": "Durable Django memory record identifier.",
        "scope_type": "Echoes the stored scope type.",
        "scope_id": "Echoes the stored scope identifier.",
        "memory_kind": "Echoes the stored memory kind.",
        "dedupe_key": "Stored dedupe or retention-bucket key for the memory.",
        "source_kind": "Provenance label describing where the memory came from.",
        "source_ref": "Optional source reference associated with the memory provenance.",
        "summary": "Stored summary text for the record.",
        "tags": "Normalized tag list stored with the record.",
        "importance": "Normalized importance score as a string decimal.",
        "pinned": "Whether the memory is pinned against normal lifecycle decay.",
        "expires_at": "Optional expiry timestamp after which the memory is ignored by active lookups.",
        "access_count": "Number of times the memory has been created or retrieved through the memory services.",
        "last_accessed_at": "Most recent timestamp when the memory was remembered or returned by search.",
    },
    "search_memory": {
        "query": "Echoes the submitted memory search text.",
        "count": "Number of results returned.",
        "results": "Concise matching memory records ordered by relevance, importance, and recency, including lifecycle metadata.",
    },
    "schedule_task": {
        "scheduled_task_id": "Durable scheduled-task identifier.",
        "recurrence_rule_id": "Linked recurrence-rule identifier used to calculate future due times.",
        "title": "Stored human-friendly task title.",
        "task_type": "Echoes the stored task type, which is always other_task.",
        "schedule_kind": "The recurring schedule model used by the task.",
        "execution_mode": "The scheduled-task execution mode, always headless_run.",
        "timezone": "The IANA timezone used to calculate due times.",
        "local_time": "A compatibility wall-clock time derived from the recurrence rule.",
        "recurrence_frequency": "The linked recurrence rule frequency.",
        "recurrence_summary": "Human-readable recurrence description for operators and UIs.",
        "next_run_at": "The next UTC datetime when the task is due.",
        "enabled": "Whether the recurring task is active.",
        "source_memory_id": "The episodic memory record created to remember the scheduling request.",
    },
    "edit_scheduled_task": {
        "scheduled_task_id": "Scheduled-task identifier returned by list_scheduled_tasks or schedule_task.",
        "title": "Updated human-friendly task title.",
        "enabled": "Whether the recurring task is active after the edit.",
        "recurrence_rule_id": "Linked recurrence-rule identifier after any recurrence change.",
        "schedule_kind": "The recurring schedule model used by the task.",
        "execution_mode": "The scheduled-task execution mode, always headless_run.",
        "timezone": "The IANA timezone used to calculate due times.",
        "local_time": "A compatibility wall-clock time derived from the recurrence rule.",
        "recurrence_summary": "Human-readable recurrence description for operators and UIs.",
        "next_run_at": "The next UTC datetime when the task is due.",
        "last_result_summary": "The last completion summary if available.",
        "last_error": "The last recorded scheduling error if available.",
    },
    "disable_scheduled_task": {
        "scheduled_task_id": "Scheduled-task identifier returned by list_scheduled_tasks or schedule_task.",
        "enabled": "Always false after the disable operation.",
        "next_run_at": "The next UTC datetime remains stored for future re-enable operations.",
        "last_result_summary": "The last completion summary if available.",
        "last_error": "The last recorded scheduling error if available.",
    },
    "enable_scheduled_task": {
        "scheduled_task_id": "Scheduled-task identifier returned by list_scheduled_tasks or schedule_task.",
        "enabled": "Always true after the enable operation.",
        "next_run_at": "The recomputed next UTC datetime after re-enabling.",
        "last_result_summary": "The last completion summary if available.",
        "last_error": "The last recorded scheduling error if available.",
    },
    "list_scheduled_tasks": {
        "count": "Number of scheduled tasks returned.",
        "results": "Concise scheduled-task records ordered by next run time, including scheduled_task_id, recurrence summaries, and run linkage.",
    },
    "spawn_subrun": {
        "parent_run_id": "The parent run that requested the child run.",
        "parent_status": "The parent run status after the child finishes or is queued.",
        "child_run_id": "The spawned child run identifier.",
        "child_status": "The child run status after the tool completes.",
        "child_execution_mode": "Whether the child runs headlessly or under a matching run execution mode.",
        "join_policy": "The join policy applied to the parent/child relationship.",
        "failure_policy": "The failure policy applied to the child group.",
        "completed_inline": "True when the child was executed immediately inside the parent tool call.",
        "resumed_parent": "True when the parent left WAITING_FOR_SUBRUN during the tool call.",
        "child_final_text": "Final assistant text produced by the child when it completed inline.",
        "child_error_summary": "Child error summary when the inline child failed or returned an error.",
        "child_failure": "Structured child failure diagnostics including classification, request_id, retryable flag, and recommended action when available.",
        "child_retryable": "True when the child failure looks transient and the parent may reasonably retry.",
        "child_recommended_action": "Short operator/model guidance for what to do next after a child failure.",
    },
    "get_current_datetime": {
        "datetime": "ISO 8601 local datetime string in the Tango timezone.",
        "timezone": "The IANA timezone used to format the returned local datetime.",
    },
    "file_write": {
        "resolved_path": "The exact filesystem path that was ultimately written.",
        "requested_path": "The path value supplied by the caller.",
        "requested_root": "The root value supplied by the caller, if any.",
        "resolved_root": "The resolved parent/root directory used for the write.",
        "changed_paths": "The paths modified by the write operation.",
        "created": "True when the file did not exist before this write.",
        "overwritten": "True when an existing file was replaced.",
        "bytes_written": "Number of bytes written to disk.",
        "sha256": "Checksum of the written content.",
    },
    "file_patch": {
        "requested_path": "The path value supplied by the caller.",
        "resolved_path": "The exact filesystem path that was patched.",
        "requested_root": "Reserved for parity with other file tools; null for file_patch.",
        "resolved_root": "The resolved parent directory of the patched file.",
        "requested_repo_dir": "The caller-supplied repo_dir when present; otherwise a path-root fallback for temp or non-repo targets.",
        "resolved_repo_dir": "The resolved repository directory or path-root fallback used as the patch base.",
        "changed_paths": "The paths modified by the patch operation.",
        "applied": "True when every hunk applied cleanly.",
        "applied_partially": "True when some hunks applied and rejects were produced.",
        "backup_path": "Backup copy path when backup=true.",
        "rejects_path": "Reject file path when a hunk fails.",
    },
    "file_delete": {
        "resolved_path": "The exact filesystem path that was targeted for deletion.",
        "requested_path": "The path value supplied by the caller.",
        "requested_root": "The root value supplied by the caller, if any.",
        "resolved_root": "The resolved parent/root directory used for the delete.",
        "changed_paths": "The paths removed by the delete operation.",
        "deleted": "True when a file or directory was removed.",
        "missing": "True when missing_ok=true and the target did not exist.",
        "deleted_type": "Whether the deleted target was a file or directory.",
    },
    "coverage_runner": {
        "total_percent": "Overall measured coverage percentage from coverage.json.",
        "files": "Per-file coverage summaries extracted from coverage.json.",
        "coverage_json_path": "Filesystem path to the generated coverage.json artifact.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
        "requested_args": "The argument list supplied by the caller.",
        "stdout": "Stdout from the coverage run command.",
        "python_interpreter": "Interpreter path used for Python-backed coverage commands.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "test_runner": {
        "summary": "Parsed pytest counts when parse=pytest.",
        "failed_tests": "Structured failure entries extracted from pytest output.",
        "exit_code": "Process exit status for the test run.",
        "stdout": "Captured test output.",
        "stderr": "Captured error output, if any.",
        "python_interpreter": "Interpreter path used for pytest mode.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "run_command_safe": {
        "ok": "True when the bounded command completed successfully with exit code 0.",
        "exit_code": "The process exit code, or null if execution was rejected or timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text or policy rejection message.",
        "timed_out": "True when the process exceeded its timeout.",
        "truncated": "True when stdout or stderr capture was truncated.",
        "normalized_command": "Normalized argv that was validated and, if allowed, executed.",
        "policy_reason": "Policy rejection reason when the request was blocked before execution.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
    },
    "run_tests": {
        "ok": "True when every requested suite completed with exit code 0.",
        "results": "Sequential per-suite execution results including script path, exit code, stdout, stderr, timeout, and duration.",
    },
    "format_runner": {
        "changed_files": "Files detected as changed by formatter output parsing.",
        "parse_mode": "The formatter/parser mode used to interpret output.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
        "requested_paths": "The path list supplied by the caller.",
        "resolved_paths": "The resolved absolute paths used for execution.",
        "command": "The exact command line executed by the runner.",
        "stdout": "Captured formatter stdout.",
        "stderr": "Captured formatter stderr.",
        "python_interpreter": "Interpreter path used for Python-backed formatter modes such as ruff_format.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "lint_runner": {
        "exit_code": "Process exit status, or null if the linter timed out.",
        "timed_out": "True when the linter exceeded its timeout.",
        "issues": "Parsed lint findings when the selected parser supports it.",
        "parse_mode": "The parser mode requested for result parsing.",
        "parse_source": "Whether parsing succeeded from stdout, stderr, or not at all.",
        "parse_warning": "Parsing warning when the output was truncated or invalid for the selected parser.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
        "requested_paths": "The path list supplied by the caller.",
        "resolved_paths": "The resolved absolute paths used for execution.",
        "command": "The exact command line executed by the runner.",
        "stdout": "Captured linter stdout.",
        "stderr": "Captured linter stderr.",
        "python_interpreter": "Interpreter path used for Python-backed linter modes such as ruff.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "typecheck_runner": {
        "exit_code": "Process exit status, or null if the type checker timed out.",
        "timed_out": "True when the type checker exceeded its timeout.",
        "diagnostics": "Parsed type-check findings when the selected parser supports it.",
        "parse_mode": "The parser mode requested for result parsing.",
        "parse_source": "Whether parsing succeeded from stdout, stderr, or not at all.",
        "parse_warning": "Parsing warning when the output could not be interpreted for the selected parser.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
        "stdout": "Captured type-check stdout.",
        "stderr": "Captured type-check stderr.",
        "python_interpreter": "Interpreter path used for Python-backed type checkers such as mypy and pyright.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "webhook": {
        "accepted": "True when the endpoint accepted the webhook payload.",
        "tool": "Always echoes `webhook` for this endpoint.",
        "event": "Echoes the submitted event name.",
        "run_id": "Echoes the submitted run identifier.",
        "payload": "Echoes the optional nested payload object.",
    },
    "git_status": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "branch": "Current branch metadata including name, upstream, ahead/behind, head_oid, and detached.",
        "requested_porcelain": "The porcelain version requested by the caller. The tool normalizes status parsing to porcelain v2 internally.",
        "is_clean": "True when there are no staged, unstaged, conflict, or requested untracked changes.",
        "staged": "List of staged paths.",
        "unstaged": "List of unstaged paths.",
        "untracked": "List of untracked paths when include_untracked=true.",
        "conflicts": "List of conflicting paths.",
        "raw": "Raw git status stdout/stderr plus truncation flags.",
    },
    "git_add": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "requested_paths": "The paths list supplied by the caller.",
        "resolved_paths": "The normalized relative paths staged by git add.",
        "changed_paths": "The paths staged by git add.",
        "staged_paths": "Normalized relative paths passed to git add when the request targeted explicit files.",
        "raw": "Raw git add stdout/stderr plus truncation flags.",
    },
    "git_commit": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "requested_paths": "The paths_to_add list supplied by the caller.",
        "resolved_paths": "The normalized relative paths staged before commit creation.",
        "changed_paths": "The paths included in the resulting commit.",
        "commit_oid": "OID of the newly created commit.",
        "summary": "First line of the commit message.",
        "changed_files": "Count of files changed by the commit.",
        "changed_files_truncated": "True when the changed-file listing hit output limits.",
        "raw": "Raw git commit stdout/stderr plus truncation flags.",
    },
    "git_push": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "remote": "The remote name that was pushed to.",
        "ref": "The ref that was pushed.",
        "pushed": "True when the push command completed successfully.",
        "raw": "Raw git push stdout/stderr plus truncation flags.",
    },
    "git_diff": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "staged": "Whether the diff was collected from the staged index.",
        "paths": "Normalized relative target paths when the request was path-scoped.",
        "requested_paths": "The paths list supplied by the caller.",
        "resolved_paths": "The resolved relative paths diffed by git.",
        "diff": "Unified diff text.",
        "truncated": "True when stdout capture truncated the diff payload.",
        "raw": "Raw git diff stdout/stderr plus truncation flags.",
    },
    "git_log": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "ref": "The ref or revision that was logged.",
        "max_count": "Maximum commit count requested.",
        "commits": "Structured commit metadata including oid, author_name, author_email, author_time_epoch, author_time_iso, and subject.",
        "parse_stats": "Counts for skipped malformed records and invalid author timestamps.",
        "parse_warning": "Optional parse warning string when stdout was truncated or malformed records were encountered.",
        "raw": "Raw git log stdout/stderr plus truncation flags.",
    },
    "git_apply": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "strip_prefix": "The strip-prefix value used for git apply.",
        "check_passed": "True when check=true and the patch validated successfully; otherwise null when not in check mode.",
        "applied": "True when the patch was actually applied.",
        "changed_paths": "The paths touched by the applied patch.",
        "touched_paths": "File paths extracted from the unified diff headers.",
        "rejects_created": "True when new .rej files were produced.",
        "reject_paths": "Relative reject file paths created by the apply operation.",
        "raw": "Raw git apply stdout/stderr plus truncation flags.",
    },
    "git_branch_create": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "name": "The branch name that was created.",
        "checked_out": "True when checkout=true and the new branch was switched to successfully.",
        "error.details.phase": "On timeout errors, indicates whether the timeout happened during branch creation or checkout.",
        "error.details.timed_out": "On timeout errors, explicitly true so callers can distinguish timeout from repository-state failures.",
    },
    "git_checkout": {
        "requested_repo_dir": "The repo_dir value supplied by the caller.",
        "resolved_repo_dir": "The resolved absolute repository directory used for execution.",
        "ref": "The ref or branch that checkout targeted.",
        "detached": "True when Git reported a detached HEAD state after checkout.",
        "raw": "Raw git checkout stdout/stderr plus truncation flags.",
    },
    "run_command": {
        "exit_code": "The process exit code, or null if the process timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text.",
        "requested_cwd": "The cwd value supplied by the caller.",
        "resolved_cwd": "The resolved absolute cwd used for execution.",
        "timed_out": "True when the process exceeded its timeout.",
        "timeout_source": "Which timeout setting was enforced for the command.",
    },
}

TOOL_REGISTRY = [
    {
        "name": "File Read Operations",
        "description": "Inspect files and repository metadata without modifying content.",
        "tools": [
            {
                "name": "file_read",
                "description": "Read a local file path.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path or repo-relative path to read.",
                        }
                    },
                    "required": ["path"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "repo_tree",
                "description": "List repository tree contents.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute or repo-relative path to list.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3,
                            "description": "Maximum depth to traverse (0 shows only the root).",
                        },
                        "include_files": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether to include files in the output.",
                        },
                    },
                },
                "released": True,
            },
        ],
    },
    {
        "name": "Code Navigation",
        "description": "Find files, symbols, and references quickly without scanning repository text by hand. Use independent navigation calls in parallel when they do not depend on one another; use them sequentially when the next call depends on the previous result.",
        "tools": [
            {
                "name": "search_files",
                "description": "Search repository paths and file names by literal text, glob-style patterns, or regex. Use this when you need to locate files, directories, or a root scope by name or path. Do not use it for content search; use `search_code` instead. This tool matches names and paths only and does not search file contents. Hidden paths are included unless they are excluded by the default ignore rules; Test paths are included by default unless `include_tests` is turned off. Use `scope` as the canonical input name for the search root. Rank expectations are exact path/name matches first, then fuzzy/partial matches. Search one path/name query at a time; if you need multiple targets, make separate calls or use regex mode with `|` for alternatives, for example `code_navigation.py|run_command_safe`. In regex mode, exact path/name hits still sort ahead of fuzzy or partial matches. Use sequentially when the next navigation step depends on this result; independent navigation lookups can be parallelized. Compact mode returns a standardized envelope with `tool`, `compact`, `query`, `requested_scope`, `resolved_scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`. For exact file hits, `selection_excerpt` may be `exact file match` so the compact result is self-explanatory.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Filename, partial path, glob-style pattern, or regex to match. Use this for path/name lookup only, not content search. Search one path/name query at a time; use separate calls for unrelated targets. In regex mode, use `|` for alternation, for example `code_navigation.py|run_command_safe`; exact path/name hits still sort ahead of fuzzy or partial matches.",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Canonical file, directory, or root scope to search from. Repo-relative values are preferred.",
                        },
                        "compact": {
                            "type": "boolean",
                            "default": False,
                            "description": "Return a smaller standardized items payload when true.",
                        },
                        "include_files": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether file matches should be included.",
                        },
                        "include_dirs": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether directory matches should be included.",
                        },
                        "is_regex": {
                            "type": "boolean",
                            "default": False,
                            "description": "Treat the query as a regex when true.",
                        },
                        "case_sensitive": {
                            "type": "boolean",
                            "default": False,
                            "description": "Use case-sensitive matching when true.",
                        },
                        "include_tests": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether test paths are included. Test paths are included by default.",
                        },
                        "include_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters that must match.",
                        },
                        "exclude_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters to exclude.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 100,
                            "description": "Top-N limit for returned matches.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Maximum directory depth to traverse.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3000,
                            "description": "Search timeout in milliseconds.",
                        },
                    },
                },
            },
            {
                "name": "list_symbols",
                "description": "List symbols defined in a file, directory, or repo root scope without reading every file manually. Use this when you already know the file or subtree and want a symbol outline. Do not use it for text search; use `search_code` or `search_files` instead. Use `scope` as the canonical input name for the symbol scope. Hidden paths are included unless excluded by the default ignore rules; test paths are included by default unless `include_tests` is turned off. Use sequentially when the next navigation step depends on this result; otherwise run independent lookups in parallel. Compact mode returns a standardized envelope with `tool`, `compact`, `query`, `requested_scope`, `resolved_scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`. In compact mode, symbol items include defining file, line, column, container/scope, and signature. For exact file or directory scopes, `selection_excerpt` may be an exact-scope marker rather than a line excerpt. Ordering is stable and grouped by path.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "Canonical file, directory, or root scope. Repo-relative values are preferred.",
                        },
                        "compact": {
                            "type": "boolean",
                            "default": False,
                            "description": "Return a smaller standardized items payload when true.",
                        },
                        "include_private": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include private symbols starting with underscore.",
                        },
                        "include_docstrings": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include docstring summaries when available.",
                        },
                        "include_tests": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether test paths are included. Test paths are included by default.",
                        },
                        "language": {
                            "type": "string",
                            "description": "Optional language filter such as python or markdown.",
                        },
                        "include_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters that must match.",
                        },
                        "exclude_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters to exclude.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 100,
                            "description": "Top-N limit for symbols returned per file and overall.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Maximum directory depth to traverse.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3000,
                            "description": "Listing timeout in milliseconds.",
                        },
                    },
                },
            },
            {
                "name": "find_symbol",
                "description": "Find a symbol definition by exact or fuzzy name. Use this when you know the symbol name and want the definition. Do not use it for content search; use `search_code` instead. The tool supports exact or fuzzy symbol matching and ranks exact matches above fuzzy matches when both are available. Use `scope` as the canonical input name for the symbol scope. Use this when the scope is a file, directory, or repo root and you want symbol resolution within that scope. Hidden paths are included unless excluded by the default ignore rules; test paths are included by default unless `include_tests` is turned off. Use sequentially when the next navigation step depends on this result; otherwise run independent lookups in parallel. Compact mode returns a standardized envelope with `tool`, `compact`, `query`, `requested_scope`, `resolved_scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`. In compact mode, symbol matches include defining file, line, column, container/scope, and signature. Ordering is stable and exact matches come first.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol_name"],
                    "properties": {
                        "symbol_name": {
                            "type": "string",
                            "description": "Symbol name or qualified symbol to locate.",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Canonical file, directory, or root scope to narrow the search. Repo-relative values are preferred.",
                        },
                        "compact": {
                            "type": "boolean",
                            "default": False,
                            "description": "Return a smaller standardized items payload when true.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Optional symbol kind filter such as function or class.",
                        },
                        "language": {
                            "type": "string",
                            "description": "Optional language filter such as python or markdown.",
                        },
                        "fuzzy": {
                            "type": "boolean",
                            "default": False,
                            "description": "Allow approximate matching when true.",
                        },
                        "include_private": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include private symbols starting with underscore.",
                        },
                        "include_tests": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether test paths are included. Test paths are included by default.",
                        },
                        "include_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters that must match.",
                        },
                        "exclude_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters to exclude.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 20,
                            "description": "Top-N limit for returned matches.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Maximum directory depth to traverse.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3000,
                            "description": "Lookup timeout in milliseconds.",
                        },
                    },
                },
            },
            {
                "name": "find_references",
                "description": "Find where a symbol is used across the repository for impact analysis. Use this when you want to understand impact before changing a symbol. Do not use it for content search; use `search_code` instead. Use `scope` as the canonical input name for the reference scope. Use this when the scope is a file, directory, or repo root and you want usage references within that scope. Use sequentially when the next navigation step depends on this result; otherwise run independent lookups in parallel. Compact mode returns a standardized envelope with `tool`, `compact`, `query`, `requested_scope`, `resolved_scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`. In compact mode, each reference item includes a short line-numbered excerpt, and the first hit is also surfaced in `selection` and `selection_excerpt`. Ordering is stable and deterministic.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to search for across the repository.",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Canonical file, directory, or root scope to narrow the search. Repo-relative values are preferred.",
                        },
                        "compact": {
                            "type": "boolean",
                            "default": False,
                            "description": "Return a smaller standardized items payload when true.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Optional symbol kind filter or usage label.",
                        },
                        "include_declarations": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include declarations as well as uses.",
                        },
                        "include_comments": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include comment mentions.",
                        },
                        "include_strings": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include string literal mentions.",
                        },
                        "include_tests": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether test paths are included. Test paths are included by default.",
                        },
                        "include_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters that must match.",
                        },
                        "exclude_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters to exclude.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 50,
                            "description": "Top-N limit for returned references.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Maximum directory depth to traverse.",
                        },
                        "context_lines": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Number of surrounding lines to include around each hit.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3000,
                            "description": "Lookup timeout in milliseconds.",
                        },
                    },
                },
            },
            {
                "name": "jump_to_symbol",
                "description": "Resolve a symbol and return the most relevant definition with nearby context. Use this when you want the best definition plus a small excerpt. Do not use it for content search; use `search_code` instead. Use `scope` as the canonical input name for the symbol scope. Use this when the scope is a file, directory, or repo root and you want the best symbol definition within that scope. Hidden paths are included unless excluded by the default ignore rules; test paths are included by default unless `include_tests` is turned off. Use sequentially when the next navigation step depends on this result; otherwise run independent lookups in parallel. Compact mode returns a standardized envelope with `tool`, `compact`, `query`, `requested_scope`, `resolved_scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`. In compact mode, the selected definition includes defining file, line, column, container/scope, signature, and a short line-numbered excerpt; if the source cannot be read, the excerpt may fall back to a one-line symbol marker. Ordering is stable and the best match comes first.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol name to resolve."},
                        "scope": {
                            "type": "string",
                            "description": "Canonical file, directory, or root scope to narrow the search. Repo-relative values are preferred.",
                        },
                        "compact": {
                            "type": "boolean",
                            "default": False,
                            "description": "Return a smaller standardized items payload when true.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Optional symbol kind filter such as function or class.",
                        },
                        "fuzzy": {
                            "type": "boolean",
                            "default": False,
                            "description": "Allow approximate matching when true.",
                        },
                        "include_private": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include private symbols starting with underscore.",
                        },
                        "include_tests": {
                            "type": "boolean",
                            "default": True,
                            "description": "Whether test paths are included. Test paths are included by default.",
                        },
                        "include_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters that must match.",
                        },
                        "exclude_globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional glob filters to exclude.",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 5,
                            "description": "Top-N limit for candidate matches returned by the lookup.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Maximum directory depth to traverse.",
                        },
                        "context_lines": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 8,
                            "description": "Number of surrounding lines to include in the excerpt.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 3000,
                            "description": "Lookup timeout in milliseconds.",
                        },
                    },
                },
            },
        ],
    },
    {
        "name": "File Write Operations",
        "description": "Modify files in the workspace while keeping the safety reviewable.",
        "tools": [
            {
                "name": "file_write",
                "description": "Write text to a file.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path or repo-relative path to write.",
                        },
                        "absolute_root": {
                            "type": "string",
                            "description": "Optional absolute base directory for resolving relative paths.",
                        },
                        "content": {"type": "string"},
                        "overwrite": {
                            "type": "boolean",
                            "description": "Optional. When true, replace an existing file instead of failing on overwrite.",
                        },
                    },
                    "required": ["path", "content"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "file_patch",
                "description": "Apply a unified diff patch to the repository.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path or repo-relative path to patch.",
                        },
                        "absolute_root": {
                            "type": "string",
                            "description": "Optional absolute base directory for resolving relative paths.",
                        },
                        "patch_unified": {"type": "string"},
                        "strip_prefix": {"type": "integer", "minimum": 0},
                        "fail_on_reject": {"type": "boolean"},
                        "expected_sha256": {"type": "string"},
                        "create_if_missing": {"type": "boolean"},
                        "backup": {"type": "boolean"},
                    },
                    "required": ["path", "patch_unified"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "file_delete",
                "description": "Delete a file or directory.",
                "risk": ToolRisk.DANGEROUS,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path or repo-relative path to delete.",
                        },
                        "absolute_root": {
                            "type": "string",
                            "description": "Optional absolute base directory for resolving relative paths.",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Delete directories recursively when true.",
                        },
                        "missing_ok": {
                            "type": "boolean",
                            "description": "Treat a missing target as a successful no-op.",
                        },
                    },
                    "required": ["path"],
                },
                "requires_approval": True,
                "released": True,
            },
        ],
    },
    {
        "name": "Git",
        "description": "Interact with git repositories hosted in the workspace.",
        "tools": [
            {
                "name": "git_add",
                "description": "Stage files for commit.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Absolute or repo-relative paths inside the selected repository.",
                        },
                        "all": {"type": "boolean"},
                        "intent_to_add": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "git_status",
                "description": "Inspect the status of the repository branch.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "porcelain": {"type": "string", "enum": ["v1", "v2"]},
                        "include_untracked": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "git_diff",
                "description": "Show diffs for staged/unstaged files.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "staged": {"type": "boolean"},
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Absolute or repo-relative paths inside the selected repository.",
                        },
                        "context_lines": {"type": "integer", "minimum": 0},
                        "detect_renames": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "git_log",
                "description": "Read recent commit metadata.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "max_count": {"type": "integer", "minimum": 1},
                        "ref": {"type": "string"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "git_apply",
                "description": "Apply a patch file to the repository.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "patch_unified": {"type": "string"},
                        "strip_prefix": {"type": "integer", "minimum": 0},
                        "reject": {"type": "boolean"},
                        "check": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["patch_unified"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "git_branch_create",
                "description": "Create a new branch and optionally switch to it.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "name": {"type": "string"},
                        "start_point": {"type": "string"},
                        "checkout": {"type": "boolean"},
                        "force": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["name"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "git_checkout",
                "description": "Switch to a branch or commit reference.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "ref": {"type": "string"},
                        "create": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["ref"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "git_commit",
                "description": "Create a new git commit.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "message": {"type": "string"},
                        "paths_to_add": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Absolute or repo-relative paths inside the selected repository.",
                        },
                        "add_all": {"type": "boolean"},
                        "signoff": {"type": "boolean"},
                        "amend": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["message"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "git_push",
                "description": "Push commits to a remote repository.",
                "risk": ToolRisk.DANGEROUS,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "repo_dir": {
                            "type": "string",
                            "description": "Absolute or repo-relative repository path.",
                        },
                        "remote": {"type": "string"},
                        "ref": {"type": "string"},
                        "set_upstream": {"type": "boolean"},
                        "force": {"type": "boolean"},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["ref"],
                },
                "requires_approval": True,
                "released": True,
            },
        ],
    },
    {
        "name": "Research",
        "description": "Search the public web and fetch readable page content.",
        "tools": [
            {
                "name": "web_search",
                "description": "Search the web for recent or reference information.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "fetch_url",
                "description": "Fetch a public URL and extract readable content.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "url": {"type": "string"},
                        "extract": {
                            "type": "string",
                            "enum": ["main_text"],
                            "default": "main_text",
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50000,
                            "default": 12000,
                        },
                    },
                    "required": ["url"],
                },
                "requires_approval": False,
                "released": True,
            },
        ],
    },
    {
        "name": "Memory",
        "description": "Persist and query durable episodic, semantic, or procedural memory.",
        "tools": [
            {
                "name": "remember",
                "description": "Store a durable memory record in Django-backed long-term memory.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scope_type": {"type": "string", "enum": ["sandbox", "agent", "user"]},
                        "scope_id": {"type": "string"},
                        "memory_kind": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural"],
                        },
                        "content": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "importance": {"type": "number", "minimum": 0, "maximum": 1},
                        "summary": {"type": "string"},
                        "dedupe_key": {"type": "string"},
                        "dedupe_mode": {"type": "string", "enum": ["auto", "key", "exact", "none"]},
                        "source_kind": {"type": "string"},
                        "source_ref": {"type": "string"},
                        "pinned": {"type": "boolean"},
                        "expires_at": {"type": "string", "format": "date-time"},
                    },
                    "required": ["scope_type", "scope_id", "memory_kind", "content"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "search_memory",
                "description": "Search durable memory records by simple text matching.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "scope_type": {"type": "string", "enum": ["sandbox", "agent", "user"]},
                        "scope_id": {"type": "string"},
                        "memory_kind": {
                            "type": "string",
                            "enum": ["episodic", "semantic", "procedural"],
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    },
                    "required": ["query"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "schedule_task",
                "description": "Create a recurring task attached to the current agent and store the request as episodic memory. Scheduled tasks run headlessly. Use task_type=other_task and put structured intent in execution_payload for recurring agent work such as backups, digests, repo maintenance, delegated research, or external integrations.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "task_type": {"type": "string", "enum": ["other_task"]},
                        "execution_mode": {
                            "type": "string",
                            "enum": ["headless_run"],
                            "default": "headless_run",
                        },
                        "timezone": {
                            "type": "string",
                            "description": "Daily shorthand timezone. Optional when recurrence.timezone is provided.",
                        },
                        "local_time": {
                            "type": "string",
                            "description": "Daily shorthand local wall-clock time in HH:MM format. Optional when recurrence is provided.",
                        },
                        "recurrence": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "timezone": {"type": "string"},
                                "frequency": {
                                    "type": "string",
                                    "enum": [
                                        "hourly",
                                        "daily",
                                        "weekly",
                                        "monthly",
                                        "quarterly",
                                        "semiannual",
                                        "annual",
                                    ],
                                },
                                "interval": {"type": "integer", "minimum": 1},
                                "by_weekday": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                    },
                                },
                                "by_month_day": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 1, "maximum": 31},
                                },
                                "week_of_month": {"type": "integer", "enum": [1, 2, 3, 4, -1]},
                                "weekday_of_month": {
                                    "type": "string",
                                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                },
                                "by_month": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 1, "maximum": 12},
                                },
                                "local_time": {"type": "string"},
                                "run_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                                "window_start_time": {"type": "string"},
                                "window_end_time": {"type": "string"},
                                "start_date": {"type": "string", "format": "date"},
                                "end_date": {"type": "string", "format": "date"},
                                "is_active": {"type": "boolean"},
                            },
                        },
                        "execution_payload": {"type": "object"},
                    },
                    "required": ["task_type"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "edit_scheduled_task",
                "description": "Edit an existing scheduled task attached to the current agent. Use this to change the title, recurrence, timezone, local_time, enabled state, or execution_payload without creating a new task. If a timezone is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC. If the schedule depends on a relative date like tomorrow or next Friday and the current local date is not already known, call `get_current_datetime` first and anchor the schedule in that local time.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scheduled_task_id": {"type": "string"},
                        "title": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "timezone": {"type": "string"},
                        "local_time": {"type": "string"},
                        "recurrence": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "timezone": {"type": "string"},
                                "frequency": {
                                    "type": "string",
                                    "enum": [
                                        "hourly",
                                        "daily",
                                        "weekly",
                                        "monthly",
                                        "quarterly",
                                        "semiannual",
                                        "annual",
                                    ],
                                },
                                "interval": {"type": "integer", "minimum": 1},
                                "by_weekday": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                    },
                                },
                                "by_month_day": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 1, "maximum": 31},
                                },
                                "week_of_month": {"type": "integer", "enum": [1, 2, 3, 4, -1]},
                                "weekday_of_month": {
                                    "type": "string",
                                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                },
                                "by_month": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 1, "maximum": 12},
                                },
                                "local_time": {"type": "string"},
                                "run_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                                "window_start_time": {"type": "string"},
                                "window_end_time": {"type": "string"},
                                "start_date": {"type": "string", "format": "date"},
                                "end_date": {"type": "string", "format": "date"},
                                "is_active": {"type": "boolean"},
                            },
                        },
                        "execution_payload": {"type": "object"},
                        "delivery_target": {"type": "string"},
                    },
                    "required": ["scheduled_task_id"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "disable_scheduled_task",
                "description": "Soft delete a scheduled task by setting enabled=false while preserving task history and identifiers.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scheduled_task_id": {"type": "string"},
                    },
                    "required": ["scheduled_task_id"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "enable_scheduled_task",
                "description": "Re-enable a disabled scheduled task and recompute its next run time from its recurrence rule.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "scheduled_task_id": {"type": "string"},
                    },
                    "required": ["scheduled_task_id"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "list_scheduled_tasks",
                "description": "List recurring tasks attached to the current agent.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled_only": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "spawn_subrun",
                "description": "Spawn a child run for focused research or delegated work and wait for its result.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "input_text": {"type": "string"},
                        "metadata": {"type": "object"},
                        "join_policy": {
                            "type": "string",
                            "enum": ["WAIT_ALL", "WAIT_ANY", "QUORUM", "TIMEOUT"],
                            "default": "WAIT_ALL",
                        },
                        "quorum": {"type": "integer", "minimum": 1},
                        "timeout_seconds": {"type": "integer", "minimum": 1},
                        "failure_policy": {
                            "type": "string",
                            "enum": ["FAIL_FAST", "CANCEL_SIBLINGS", "IGNORE_FAILURE"],
                            "default": "IGNORE_FAILURE",
                        },
                        "group_id": {"type": "string"},
                    },
                    "required": ["input_text"],
                },
                "requires_approval": False,
                "released": True,
            },
        ],
    },
    {
        "name": GOOGLE_BRIDGE_TOOL_GROUP_NAME,
        "description": 'Google bridge for Gmail, Calendar, Drive, Docs, and Sheets reads plus Gmail draft/send/trash/delete workflows and Calendar read/create/update/delete workflows. Bare Gmail list reads default to unread messages. Use include_read=true when you want all Gmail messages, or provide a query/label filter for a narrower mailbox view. Gmail list/read query filters support exact sender, sender domain, subject, and top-level OR splitting across those clauses. Use account_scope=all when you want the bridge to fan out across every active connected account and merge the results. For Gmail trash/delete queries, use account_scope=all when you want the same query to apply across every connected account. Calendar list reads can omit calendar_id or use all to inspect every calendar on the connected account, while primary stays available when you explicitly want one calendar. Drive pickers and Google file attachments should normalize into the same pending attachment flow as local files. For Gmail reads, use query filters such as from:info@airbnb.com for exact sender, from:airbnb.com for sender-domain, subject:("Airbnb") for subject search, plus label_ids or include_read for mailbox filtering. For Gmail trash/delete, never use read as a lookup step. OR is supported for Gmail list/read searches and bulk trash/delete cleanup only at the top level, where it is split into separate Gmail clauses before merging or deleting. Nested OR inside parentheses is rejected as malformed. For bulk cleanup, choose the Gmail query shape that matches your intent: subject:("Airbnb") for subject-based cleanup, from:info@airbnb.com for exact sender cleanup, and from:airbnb.com for sender-domain cleanup. If you need multiple cleanup targets in one call, join them with OR and the bridge will split them into separate Gmail clauses as long as each clause is complete. For Gmail and Calendar writes, if a timezone argument is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC. For Gmail writes, create a draft first and then send it when ready. Calendar create, update, and delete workflows are supported. The preferred delete pattern in each case is action_kind=delete with operation=trash (or omit operation and let it default) plus the matching Gmail query. Keep delete_mode at trash unless you explicitly want permanent deletion. Gmail OR fan-out is capped at 10 top-level clauses by default; set TOOLRUNNER_GMAIL_OR_CLAUSE_LIMIT to adjust it. If the agent accidentally writes `from:@domain.com` or adds stray spaces after query tokens, the bridge normalizes that to the canonical Gmail form. The payload contract stays JSON-in / JSON-out so future Google surfaces can reuse the same shape.',
        "tools": [
            {
                "name": GOOGLE_BRIDGE_TOOL_NAME,
                "description": GOOGLE_BRIDGE_TOOL_DESCRIPTION,
                "risk": ToolRisk.SAFE,
                "args_schema": build_google_bridge_args_schema(),
                "requires_approval": False,
                "released": True,
            },
        ],
    },
    {
        "name": "Utilities",
        "description": "Small safe utilities for local runtime context and coordination.",
        "tools": [
            {
                "name": "get_current_datetime",
                "description": "Return the current ISO 8601 local datetime string in the Tango timezone.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                "requires_approval": False,
                "released": True,
            },
        ],
    },
    {
        "name": "Execution",
        "description": "Run Python scripts or HTTP webhooks.",
        "tools": [
            {
                "name": "python_exec",
                "description": "Execute a Python script.",
                "risk": ToolRisk.DANGEROUS,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "code": {"type": "string"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "Absolute or repo-relative file path to write before execution.",
                                    },
                                    "content_b64": {"type": "string"},
                                },
                                "required": ["path", "content_b64"],
                            },
                        },
                        "entrypoint": {
                            "type": "string",
                            "description": "Absolute or repo-relative path to the Python entrypoint file.",
                        },
                    },
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "webhook",
                "description": "Call an external webhook.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "payload": {"type": "object"}},
                    "required": ["url"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "coverage_runner",
                "description": "Run coverage collection on the repository.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": ["pytest_coverage"]},
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["kind"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "format_runner",
                "description": "Format code according to project conventions.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tool": {
                            "type": "string",
                            "enum": ["ruff_format", "black", "prettier", "command"],
                        },
                        "mode": {"type": "string", "enum": ["check", "apply"]},
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Absolute or repo-relative target paths.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Only valid when tool=command. Omit for ruff_format, black, and prettier runs.",
                        },
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                    },
                    "required": ["tool"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "lint_runner",
                "description": "Run linting task against the codebase.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tool": {
                            "type": "string",
                            "enum": ["ruff", "flake8", "eslint", "prettier", "command"],
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Absolute or repo-relative target paths.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Only valid when tool=command. Omit for ruff, flake8, eslint, and prettier runs.",
                        },
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                        "parse": {"type": "string", "enum": ["ruff", "flake8", "eslint", "none"]},
                    },
                    "required": ["tool"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "run_command",
                "description": "Run an arbitrary shell command only when no specialized tool fits.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cmd": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command as a list of strings. The first item is the executable and each following item is a separate argument.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "env": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": "Optional environment variables to add to the subprocess environment.",
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Optional command timeout in milliseconds.",
                        },
                        "max_output_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Maximum bytes to retain from stdout and stderr.",
                        },
                        "stdin_text": {
                            "type": "string",
                            "description": "Optional UTF-8 text sent to the process stdin.",
                        },
                    },
                    "required": ["cmd"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "run_command_safe",
                "description": "Run a tightly bounded developer command without approval when it fits the safe allowlist and workspace policy.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Structured argv only. The first element is the executable and each following item is a separate argument.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Repo-relative working directory inside the active workspace root.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Optional bounded timeout in seconds. Defaults to 60.",
                        },
                    },
                    "required": ["argv"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "search_code",
                "description": "Search codebase for text patterns/regex. By default the response is the concise result block; pass include_meta=true if you need the policy wrapper. When using regex mode, use `|` for alternatives instead of the word `OR`.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search pattern. Use literal text when is_regex=false, or regex syntax when is_regex=true. Prefer `|` for alternatives instead of the word `OR`.",
                        },
                        "root": {
                            "type": "string",
                            "description": "Optional absolute or repo-relative search root.",
                        },
                        "is_regex": {
                            "type": "boolean",
                            "description": "Set true to interpret query as a regex. Use regex alternation (`|`) for alternatives, not the word `OR`.",
                        },
                        "case_sensitive": {"type": "boolean"},
                        "include_globs": {"type": "array", "items": {"type": "string"}},
                        "exclude_globs": {"type": "array", "items": {"type": "string"}},
                        "max_results": {"type": "integer", "minimum": 1},
                        "max_matches_per_file": {"type": "integer", "minimum": 1},
                        "context_lines": {"type": "integer", "minimum": 0},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                    },
                    "required": ["query"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "shell_exec",
                "description": "Execute a shell command inside the workspace.",
                "risk": ToolRisk.DANGEROUS,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cmd": {"type": "array", "items": {"type": "string"}},
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "env": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "required": ["cmd"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "test_runner",
                "description": "Run the suite of automated tests.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["powershell_script", "pytest", "command"],
                        },
                        "script_path": {"type": "string"},
                        "script_args": {"type": "array", "items": {"type": "string"}},
                        "pytest_args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {"type": "array", "items": {"type": "string"}},
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "env": {"type": "object", "additionalProperties": {"type": "string"}},
                        "timeout_ms": {"type": "integer", "minimum": 0},
                        "max_output_bytes": {"type": "integer", "minimum": 1},
                        "parse": {"type": "string", "enum": ["pytest", "none"]},
                    },
                    "required": ["kind"],
                },
                "requires_approval": True,
                "released": True,
            },
            {
                "name": "run_tests",
                "description": "Run one or more repo-owned PowerShell test scripts without arbitrary PowerShell execution.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "suites": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["backend", "toolrunner", "all"]},
                            "description": "Requested repo test suites. Use `all` to run both sequentially.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Optional timeout in seconds applied to each requested suite. Defaults to 900.",
                        },
                    },
                    "required": ["suites"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "typecheck_runner",
                "description": "Run static type checking tooling.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tool": {"type": "string", "enum": ["mypy", "pyright", "tsc", "command"]},
                        "cwd": {
                            "type": "string",
                            "description": "Absolute or repo-relative working directory.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {"type": "array", "items": {"type": "string"}},
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Optional timeout in milliseconds. Defaults to 300000.",
                        },
                        "max_output_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Optional stdout/stderr capture limit in bytes. Defaults to 262144.",
                        },
                        "parse": {"type": "string", "enum": ["mypy", "pyright", "tsc", "none"]},
                    },
                    "required": ["tool"],
                },
                "requires_approval": False,
                "released": True,
            },
        ],
    },
]


for group in TOOL_REGISTRY:
    for tool in group.get("tools", []):
        schema = deepcopy(
            tool.get("args_schema")
            or {"type": "object", "properties": {}, "additionalProperties": True}
        )
        required_parameters = list(tool.get("required_parameters") or schema.get("required") or [])
        tool["required_parameters"] = required_parameters
        tool["args_schema"] = schema
        docs = _schema_docs(
            required_parameters,
            _TOOL_EXAMPLES.get(tool["name"]),
            _TOOL_RESPONSE_FIELDS.get(tool["name"]),
        )
        docs = f"{docs}{_TOOL_ADDITIONAL_DOCS.get(tool['name'], '')}"
        existing_schema_description = str(schema.get("description") or "").strip()
        schema["description"] = (
            f"{existing_schema_description}\n\n{docs}".strip()
            if existing_schema_description
            else docs
        )
        tool["description"] = schema["description"]
        response_fields = _TOOL_RESPONSE_FIELDS.get(tool["name"])
        if response_fields is not None:
            tool["response_fields"] = deepcopy(response_fields)
        examples = _TOOL_EXAMPLES.get(tool["name"])
        if examples is not None:
            if isinstance(examples, list):
                schema["examples"] = deepcopy(examples)
            else:
                schema["examples"] = [deepcopy(examples)]



