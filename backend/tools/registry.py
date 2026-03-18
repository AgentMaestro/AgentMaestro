from copy import deepcopy
import json

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
        {"path": "C:\\Dev\\AgentMaestro\\backend\\runs\\tests\\fixtures\\tool_repo", "max_depth": 3, "include_files": True},
    ],
    "file_write": [
        {"path": "notes/hello.py", "content": "print('hello')\n"},
        {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py", "content": "print('hello')\n", "overwrite": True},
    ],
    "file_delete": [
        {"path": "notes/hello.py"},
        {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py"},
    ],
    "file_patch": [
        {"path": "notes/hello.py", "patch_unified": "--- a/notes/hello.py\n+++ b/notes/hello.py\n@@ -1,1 +1,1 @@\n-print('hello')\n+print('hello world')\n"},
        {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py", "patch_unified": "--- a/hello.py\n+++ b/hello.py\n@@ -1,1 +1,1 @@\n-print('hello')\n+print('hello world')\n"},
    ],
    "git_add": [
        {"repo_dir": ".", "paths": ["backend/tools/admin.py"]},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"]},
    ],
    "git_status": [
        {"repo_dir": ".", "porcelain": "v1", "include_untracked": True},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "porcelain": "v1", "include_untracked": True},
    ],
    "git_diff": [
        {"repo_dir": ".", "paths": ["backend/tools/admin.py"], "staged": False},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"], "staged": False},
    ],
    "git_log": [
        {"repo_dir": ".", "max_count": 5},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "max_count": 5},
    ],
    "git_apply": [
        {"repo_dir": ".", "patch_unified": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n"},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "patch_unified": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n"},
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
        {"repo_dir": "C:\\Dev\\AgentMaestro", "message": "Smoke test commit", "paths_to_add": ["C:\\Dev\\AgentMaestro\\backend\\tools\\admin.py"]},
    ],
    "git_push": [
        {"repo_dir": ".", "remote": "origin", "ref": "main"},
        {"repo_dir": "C:\\Dev\\AgentMaestro", "remote": "origin", "ref": "main"},
    ],
    "python_exec": [
        {"code": "from pathlib import Path\nprint(Path('README.md').exists())"},
        {"files": [{"path": "scripts/hello.py", "content_b64": "cHJpbnQoJ2hlbGxvJykK"}], "entrypoint": "scripts/hello.py"},
    ],
    "webhook": {"url": "https://example.test/webhook", "payload": {"event": "smoke"}},
    "coverage_runner": [
        {"kind": "pytest_coverage", "cwd": ".", "args": ["toolrunner/app/tests/test_file_write.py"], "timeout_ms": 600000},
        {"kind": "pytest_coverage", "cwd": "C:\\Dev\\AgentMaestro", "args": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"], "timeout_ms": 600000},
    ],
    "format_runner": [
        {"tool": "ruff_format", "mode": "apply", "cwd": ".", "paths": ["toolrunner/app/tests/test_file_write.py"]},
        {"tool": "ruff_format", "mode": "apply", "cwd": "C:\\Dev\\AgentMaestro", "paths": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"]},
    ],
    "lint_runner": [
        {"tool": "ruff", "cwd": ".", "paths": ["backend/tools"]},
        {"tool": "ruff", "cwd": "C:\\Dev\\AgentMaestro", "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools"]},
    ],
    "run_command": {
        "cmd": ["cmd", "/C", "echo RUN_COMMAND_SMOKE_OK && dir C:\\Dev\\AgentMaestro\\toolrunner\\app\\tools"],
        "cwd": ".",
    },
    "run_command_safe": {
        "argv": ["python", "manage.py", "check"],
        "cwd": ".",
        "timeout_seconds": 60,
    },
    "search_code": [
        {"query": "provider_call_id", "root": "backend", "include_globs": ["**/*.py"]},
        {"query": "provider_call_id", "root": "C:\\Dev\\AgentMaestro\\backend", "include_globs": ["**/*.py"]},
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
            "title": "daily weather report for Richmond, VA",
            "task_type": "daily_weather_report",
            "execution_mode": "deterministic",
            "timezone": "America/New_York",
            "local_time": "08:00",
            "execution_payload": {
                "location": "Richmond, VA",
                "query": "site:weather.com Richmond VA daily and weekly weather forecast",
                "source_domain": "weather.com"
            }
        },
        {
            "title": "daily repo backup summary",
            "task_type": "other_daily_task",
            "execution_mode": "headless_run",
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "daily",
                "interval": 1,
                "local_time": "05:00"
            },
            "execution_payload": {
                "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
                "repo_dir": "C:/Dev/AgentMaestro",
                "notes": "Use git status and git log, then create a concise backup commit if there are changes."
            }
        },
        {
            "title": "coach weather checks",
            "task_type": "daily_weather_report",
            "execution_mode": "deterministic",
            "recurrence": {
                "timezone": "America/New_York",
                "frequency": "hourly",
                "interval": 1,
                "by_weekday": ["mon", "wed", "fri", "sat"],
                "run_minute": 0,
                "window_start_time": "09:00",
                "window_end_time": "19:00"
            },
            "execution_payload": {
                "location": "Richmond, VA",
                "source_domain": "weather.com"
            }
        }
    ],
    "list_scheduled_tasks": {
        "enabled_only": True,
        "limit": 10
    },
    "spawn_subrun": {
        "input_text": "Research the current weather outlook for Ocala tennis conditions and return a concise summary.",
        "metadata": {"purpose": "focused research", "topic": "weather"},
        "join_policy": "WAIT_ALL",
        "failure_policy": "IGNORE_FAILURE"
    },
    "shell_exec": [
        {"cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"], "cwd": "."},
        {"cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"], "cwd": "C:\\Dev\\AgentMaestro"},
    ],
    "test_runner": [
        {"kind": "pytest", "pytest_args": ["toolrunner/app/tests/test_file_write.py"], "cwd": ".", "parse": "pytest", "timeout_ms": 600000},
        {"kind": "pytest", "pytest_args": ["C:\\Dev\\AgentMaestro\\toolrunner\\app\\tests\\test_file_write.py"], "cwd": "C:\\Dev\\AgentMaestro", "parse": "pytest", "timeout_ms": 600000},
    ],
    "run_tests": {
        "suites": ["backend"],
        "timeout_seconds": 900,
    },
    "typecheck_runner": [
        {"tool": "mypy", "cwd": ".", "args": ["backend"], "timeout_ms": 300000, "max_output_bytes": 262144},
        {"tool": "mypy", "cwd": "C:\\Dev\\AgentMaestro", "args": ["C:\\Dev\\AgentMaestro\\backend"], "timeout_ms": 300000, "max_output_bytes": 262144},
    ],
}

_TOOL_ADDITIONAL_DOCS = {
    "file_read": "\n\nPATH NOTES:\n"
    "- `path` may be absolute or repo-relative.\n"
    "- Repo-relative paths resolve from the repository root when one is provided in policy context.",
    "repo_tree": "\n\nPATH NOTES:\n"
    "- `path` may be absolute or repo-relative.\n"
    "- Repo-relative paths resolve from the repository root when one is provided in policy context.",
    "file_write": "\n\nPATH NOTES:\n"
    "- `path` may be absolute or repo-relative.\n"
    "- Repo-relative paths resolve from the repository root when one is provided in policy context.\n\n"
    "WRITE MODE NOTES:\n"
    "- `overwrite` is optional.\n"
    "- Leave `overwrite=false` to avoid replacing an existing file.\n"
    "- Set `overwrite=true` when you intentionally want to replace an existing file instead of deleting it first.\n"
    "- Use `file_delete` only when the goal is to remove the file entirely.",
    "file_patch": "\n\nPATCH FORMAT NOTES:\n"
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
    "test_runner": "\n\nRUN MODE NOTES:\n"
    "- `kind` is required.\n"
    "- Supported kinds are `powershell_script`, `pytest`, and `command`.\n"
    "- Choose exactly one mode:\n"
    "  - `kind=powershell_script` requires `script_path`\n"
    "  - `kind=pytest` requires `pytest_args`\n"
    "  - `kind=command` requires `cmd`\n"
    "- Prefer `kind=pytest` for narrow smoke tests and `kind=powershell_script` for repo-standard test entrypoints.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLES:\n"
    "- Pytest mode:\n"
    "  `{ \"kind\": \"pytest\", \"pytest_args\": [\"toolrunner/app/tests/test_file_write.py\"], \"cwd\": \".\", \"parse\": \"pytest\" }`\n"
    "- PowerShell script mode:\n"
    "  `{ \"kind\": \"powershell_script\", \"script_path\": \"backend/scripts/runtests.ps1\", \"cwd\": \".\" }`\n\n"
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
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    "- `{ \"kind\": \"pytest_coverage\", \"cwd\": \".\", \"args\": [\"toolrunner/app/tests/test_file_write.py\"] }`\n\n"
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
    "- Use `cmd` only when `tool=command`.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    "- `{ \"tool\": \"ruff_format\", \"mode\": \"apply\", \"cwd\": \".\", \"paths\": [\"toolrunner/app/tests/test_file_write.py\"] }`\n\n"
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
    "- Only allowlisted executables are supported: `python`, `pytest`, `ruff`, `mypy`, `uv`, and `django-admin`.\n"
    "- `git` is explicitly blocked. Use the dedicated `git_*` tools instead.\n"
    "- Shell composition, shell wrappers, package installs, migrations, dev servers, and other interactive or long-running commands are rejected.\n"
    "- `cwd` must stay inside the active workspace root.\n",
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
    "- Use `run_command` only as a last resort when no specialized tool matches the task.\n"
    "- Do not use `run_command` for Git operations that map to `git_add`, `git_status`, `git_diff`, `git_log`, `git_apply`, `git_commit`, `git_push`, `git_checkout`, or `git_branch_create`.\n"
    "- Do not use `run_command` for direct file content reads that should go through `file_read`.\n\n"
    "MINIMAL SUCCESSFUL EXAMPLE:\n"
    "- `{ \"cmd\": [\"cmd\", \"/C\", \"echo RUN_COMMAND_SMOKE_OK && dir C:\\\\Dev\\\\AgentMaestro\\\\toolrunner\\\\app\\\\tools\"], \"cwd\": \".\" }`\n\n"
    "TROUBLESHOOTING:\n"
    "- If validation fails, check that `cmd` is an array and not a single string.\n"
    "- If you need shell features such as `&&`, invoke a shell explicitly through `cmd /C` or `powershell -Command`.\n"
    "- If the command times out, inspect the returned timeout source and effective timeout value.",
    "search_code": "\n\nPATH NOTES:\n"
    "- `root` may be omitted, repo-relative, or absolute.\n"
    "- Repo-relative `root` values resolve from the repository root when one is provided in policy context.\n"
    "- Absolute `root` values are permitted only when they fall under the allowed roots for the run.\n"
    "- `include_globs` are evaluated relative to the provided `root`.\n\n"
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
    "- `schedule_task` supports both built-in deterministic jobs and general headless recurring agent work.\n"
    "- Use `task_type=daily_weather_report` with `execution_mode=deterministic` for the built-in weather scheduler.\n"
    "- Use `task_type=other_daily_task` with `execution_mode=headless_run` for general recurring work such as backups, digests, maintenance, or delegated research.\n"
    "- Prefer `recurrence` for anything more complex than a single daily wall-clock time.\n"
    "- Use `title` and `execution_payload` to describe the recurring job clearly so the future headless run has enough context.\n",
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
    "- Repo-relative `repo_dir` resolves from the repository root when one is provided in policy context.",
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
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.",
    "typecheck_runner": "\n\nPATH NOTES:\n"
    "- `cwd` may be absolute or repo-relative.\n"
    "- Path arguments passed through `args` may also be absolute or repo-relative when the underlying type checker supports them.\n\n"
    "DEFAULT LIMITS:\n"
    "- `timeout_ms` defaults to `300000`.\n"
    "- `max_output_bytes` defaults to `262144`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `mypy` and `pyright` run through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If a Python-backed type checker is missing, the tool reports the resolved interpreter path and source.",
}


_TOOL_RESPONSE_FIELDS = {
    "file_read": {
        "path": "Echoes the requested path value.",
        "mode": "Whether the response content is text or binary.",
        "content": "Returned text content for text mode.",
        "content_base64": "Returned bytes encoded as base64 for binary mode.",
        "truncated": "True when max_bytes cut the response short.",
    },
    "repo_tree": {
        "root": "The root path that was listed.",
        "entries": "Sorted tree entries returned by the tool.",
        "stats": "Counts for files, dirs, exclusions, and allowed roots used by policy.",
        "truncated": "True when max_entries limited the walk.",
    },
    "search_code": {
        "query": "Echoes the query that was searched.",
        "is_regex": "Whether regex mode was enabled for the search.",
        "case_sensitive": "Whether matching was case-sensitive.",
        "matches": "Path-sorted files with match_count and snippet metadata for each matching file.",
        "stats": "File counts, total matches, exclusions, and allowed roots used by policy.",
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
        "task_type": "Echoes the stored task type.",
        "schedule_kind": "The recurring schedule model used by the task.",
        "execution_mode": "Whether the task executes with the deterministic path or launches a headless agent run.",
        "timezone": "The IANA timezone used to calculate due times.",
        "local_time": "A compatibility wall-clock time derived from the recurrence rule.",
        "recurrence_frequency": "The linked recurrence rule frequency.",
        "recurrence_summary": "Human-readable recurrence description for operators and UIs.",
        "next_run_at": "The next UTC datetime when the task is due.",
        "enabled": "Whether the recurring task is active.",
        "source_memory_id": "The episodic memory record created to remember the scheduling request.",
    },
    "list_scheduled_tasks": {
        "count": "Number of scheduled tasks returned.",
        "results": "Concise scheduled-task records ordered by next run time, including recurrence summaries and run linkage.",
    },
    "spawn_subrun": {
        "parent_run_id": "The parent run that requested the child run.",
        "parent_status": "The parent run status after the child finishes or is queued.",
        "child_run_id": "The spawned child run identifier.",
        "child_status": "The child run status after the tool completes.",
        "child_execution_mode": "Whether the child runs headlessly or through the deterministic tick path.",
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
    "file_write": {
        "resolved_path": "The exact filesystem path that was ultimately written.",
        "created": "True when the file did not exist before this write.",
        "overwritten": "True when an existing file was replaced.",
        "bytes_written": "Number of bytes written to disk.",
        "sha256": "Checksum of the written content.",
    },
    "file_patch": {
        "path": "The requested target path.",
        "applied": "True when every hunk applied cleanly.",
        "applied_partially": "True when some hunks applied and rejects were produced.",
        "backup_path": "Backup copy path when backup=true.",
        "rejects_path": "Reject file path when a hunk fails.",
    },
    "file_delete": {
        "resolved_path": "The exact filesystem path that was targeted for deletion.",
        "deleted": "True when a file or directory was removed.",
        "missing": "True when missing_ok=true and the target did not exist.",
        "deleted_type": "Whether the deleted target was a file or directory.",
    },
    "coverage_runner": {
        "total_percent": "Overall measured coverage percentage from coverage.json.",
        "files": "Per-file coverage percentages extracted from coverage.json.",
        "coverage_json_path": "Filesystem path to the generated coverage.json artifact.",
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
    },
    "run_tests": {
        "ok": "True when every requested suite completed with exit code 0.",
        "results": "Sequential per-suite execution results including script path, exit code, stdout, stderr, timeout, and duration.",
    },
    "format_runner": {
        "changed_files": "Files detected as changed by formatter output parsing.",
        "parse_mode": "The formatter/parser mode used to interpret output.",
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
        "repo_dir": "Repository path argument echoed back in the response.",
        "branch": "Current branch metadata including name, upstream, ahead/behind, head_oid, and detached.",
        "is_clean": "True when there are no staged, unstaged, conflict, or requested untracked changes.",
        "staged": "List of staged paths.",
        "unstaged": "List of unstaged paths.",
        "untracked": "List of untracked paths when include_untracked=true.",
        "conflicts": "List of conflicting paths.",
        "raw": "Raw git status stdout/stderr plus truncation flags.",
    },
    "git_add": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "staged_paths": "Normalized relative paths passed to git add when the request targeted explicit files.",
        "raw": "Raw git add stdout/stderr plus truncation flags.",
    },
    "git_commit": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "commit_oid": "OID of the newly created commit.",
        "summary": "First line of the commit message.",
        "changed_files": "Count of files changed by the commit.",
        "changed_files_truncated": "True when the changed-file listing hit output limits.",
        "raw": "Raw git commit stdout/stderr plus truncation flags.",
    },
    "git_push": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "remote": "The remote name that was pushed to.",
        "ref": "The ref that was pushed.",
        "pushed": "True when the push command completed successfully.",
        "raw": "Raw git push stdout/stderr plus truncation flags.",
    },
    "git_diff": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "staged": "Whether the diff was collected from the staged index.",
        "paths": "Normalized relative target paths when the request was path-scoped.",
        "diff": "Unified diff text.",
        "truncated": "True when stdout capture truncated the diff payload.",
        "raw": "Raw git diff stdout/stderr plus truncation flags.",
    },
    "git_log": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "ref": "The ref or revision that was logged.",
        "max_count": "Maximum commit count requested.",
        "commits": "Structured commit metadata including oid, author_name, author_email, author_time_epoch, author_time_iso, and subject.",
        "parse_stats": "Counts for skipped malformed records and invalid author timestamps.",
        "parse_warning": "Optional parse warning string when stdout was truncated or malformed records were encountered.",
        "raw": "Raw git log stdout/stderr plus truncation flags.",
    },
    "git_apply": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "strip_prefix": "The strip-prefix value used for git apply.",
        "check_passed": "True when check=true and the patch validated successfully; otherwise null when not in check mode.",
        "applied": "True when the patch was actually applied.",
        "touched_paths": "File paths extracted from the unified diff headers.",
        "rejects_created": "True when new .rej files were produced.",
        "reject_paths": "Relative reject file paths created by the apply operation.",
        "raw": "Raw git apply stdout/stderr plus truncation flags.",
    },
    "git_branch_create": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "name": "The branch name that was created.",
        "checked_out": "True when checkout=true and the new branch was switched to successfully.",
        "error.details.phase": "On timeout errors, indicates whether the timeout happened during branch creation or checkout.",
        "error.details.timed_out": "On timeout errors, explicitly true so callers can distinguish timeout from repository-state failures.",
    },
    "git_checkout": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "ref": "The ref or branch that checkout targeted.",
        "detached": "True when Git reported a detached HEAD state after checkout.",
        "raw": "Raw git checkout stdout/stderr plus truncation flags.",
    },
    "run_command": {
        "exit_code": "The process exit code, or null if the process timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text.",
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "repo_dir": {"type": "string", "description": "Absolute or repo-relative repository path."},
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
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
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
                        "extract": {"type": "string", "enum": ["main_text"], "default": "main_text"},
                        "max_chars": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 12000},
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
                        "memory_kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
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
                        "memory_kind": {"type": "string", "enum": ["episodic", "semantic", "procedural"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    },
                    "required": ["query"],
                },
                "requires_approval": False,
                "released": True,
            },
            {
                "name": "schedule_task",
                "description": "Create a recurring task attached to the current agent and store the request as episodic memory. Use deterministic mode for built-in weather automation. Use headless_run for general recurring agent work such as backups, digests, repo maintenance, or delegated research.",
                "risk": ToolRisk.SAFE,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "task_type": {"type": "string", "enum": ["daily_weather_report", "daily_email_check", "other_daily_task"]},
                        "execution_mode": {"type": "string", "enum": ["deterministic", "headless_run"], "default": "deterministic"},
                        "timezone": {"type": "string", "description": "Daily shorthand timezone. Optional when recurrence.timezone is provided."},
                        "local_time": {"type": "string", "description": "Daily shorthand local wall-clock time in HH:MM format. Optional when recurrence is provided."},
                        "recurrence": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "timezone": {"type": "string"},
                                "frequency": {"type": "string", "enum": ["hourly", "daily", "weekly", "monthly", "quarterly", "semiannual", "annual"]},
                                "interval": {"type": "integer", "minimum": 1},
                                "by_weekday": {"type": "array", "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
                                "by_month_day": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 31}},
                                "week_of_month": {"type": "integer", "enum": [1, 2, 3, 4, -1]},
                                "weekday_of_month": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
                                "by_month": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}},
                                "local_time": {"type": "string"},
                                "run_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                                "window_start_time": {"type": "string"},
                                "window_end_time": {"type": "string"},
                                "start_date": {"type": "string", "format": "date"},
                                "end_date": {"type": "string", "format": "date"},
                                "is_active": {"type": "boolean"}
                            }
                        },
                        "execution_payload": {"type": "object"},
                    },
                    "required": ["task_type"],
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
                        "join_policy": {"type": "string", "enum": ["WAIT_ALL", "WAIT_ANY", "QUORUM", "TIMEOUT"], "default": "WAIT_ALL"},
                        "quorum": {"type": "integer", "minimum": 1},
                        "timeout_seconds": {"type": "integer", "minimum": 1},
                        "failure_policy": {"type": "string", "enum": ["FAIL_FAST", "CANCEL_SIBLINGS", "IGNORE_FAILURE"], "default": "IGNORE_FAILURE"},
                        "group_id": {"type": "string"}
                    },
                    "required": ["input_text"],
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
                                    "path": {"type": "string", "description": "Absolute or repo-relative file path to write before execution."},
                                    "content_b64": {"type": "string"},
                                },
                                "required": ["path", "content_b64"],
                            },
                        },
                        "entrypoint": {"type": "string", "description": "Absolute or repo-relative path to the Python entrypoint file."},
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
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
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
                        "tool": {"type": "string", "enum": ["ruff_format", "black", "prettier", "command"]},
                        "mode": {"type": "string", "enum": ["check", "apply"]},
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute or repo-relative target paths."},
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {"type": "array", "items": {"type": "string"}},
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
                        "tool": {"type": "string", "enum": ["ruff", "flake8", "eslint", "prettier", "command"]},
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute or repo-relative target paths."},
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {"type": "array", "items": {"type": "string"}},
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
                "description": "Search codebase for text patterns/regex.",
                "risk": ToolRisk.ELEVATED,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "root": {"type": "string", "description": "Optional absolute or repo-relative search root."},
                        "is_regex": {"type": "boolean"},
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
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
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
                        "kind": {"type": "string", "enum": ["powershell_script", "pytest", "command"]},
                        "script_path": {"type": "string"},
                        "script_args": {"type": "array", "items": {"type": "string"}},
                        "pytest_args": {"type": "array", "items": {"type": "string"}},
                        "cmd": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
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
                        "cwd": {"type": "string", "description": "Absolute or repo-relative working directory."},
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
        schema = deepcopy(tool.get("args_schema") or {"type": "object", "properties": {}, "additionalProperties": True})
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
        schema["description"] = f"{existing_schema_description}\n\n{docs}".strip() if existing_schema_description else docs
        examples = _TOOL_EXAMPLES.get(tool["name"])
        if examples is not None:
            if isinstance(examples, list):
                schema["examples"] = deepcopy(examples)
            else:
                schema["examples"] = [deepcopy(examples)]
