# ToolRunner service

This FastAPI service executes ToolRunner tool calls for AgentMaestro runs.

## Endpoints

- `POST /v1/run/tool`
  - Execute a single tool call for a run/workspace.
  - Requires signed JSON (see `auth.py` for `X-AM-Timestamp`/`X-AM-Signature`).
  - Accepts `ExecuteRequest` payload and returns `ExecuteResponse`.
- `POST /v1/run/tool/webhook`
  - Ingest webhook events (may enqueue tool runs or create resources).
  - Also signed; payload forwarded to `create_webhook`.
- Legacy aliases `/v1/execute` and `/v1/webhook` still exist while clients migrate.

## Tool list

Supported `tool_name` values include:

- `shell_exec`, `python_exec`
- `file_read`, `file_write`, `file_delete`, `file_patch`
- `repo_tree`, `search_code`, `fetch_url`, `web_search`
- `run_command`, `run_command_safe`
- `test_runner`, `run_tests`
- `format_runner`, `lint_runner`, `typecheck_runner`, `coverage_runner`
- `git_status`, `git_diff`, `git_log`, `git_apply`, `git_branch_create`, `git_checkout`, `git_commit`, `git_push`, `git_add`

Tool dispatch lives in `app/main.py`, with Pydantic args in `app/models.py` and handler implementations in `app/tools/`.

## Safe command boundaries

`run_command_safe` is the narrow no-approval command surface for bounded developer commands.

- It accepts structured `argv` only. Raw shell strings, shell wrappers, redirection, and composition tokens are rejected.
- It only allows the executable allowlist: `python`, `pytest`, `ruff`, `mypy`, `uv`, and `django-admin`.
- `git` is explicitly blocked. Use the dedicated `git_*` tools instead.
- The working directory must stay inside the active workspace root.
- Interactive shells, dev servers, package installs, migrations, and other long-running or destructive operations are rejected.

## Repo test runner

`run_tests` is intentionally narrow.

- It only runs the repo-owned PowerShell scripts:
  - `backend/scripts/runtests.ps1`
  - `toolrunner/scripts/runtests.ps1`
- If a suite only has `test.ps1`, the tool detects and uses that fallback path.
- Suites run sequentially and return structured per-suite results.

## Signature

Requests must include:

- `X-AM-Timestamp`: UNIX epoch seconds (within 60s skew)
- `X-AM-Signature`: `HMAC_SHA256(secret, f"{timestamp}.{body}")`

Use `TOOLRUNNER_SECRET` (default `insecure-secret`) to compute/verify.

## Developer notes

- Shared subprocess capture/truncation lives in `app/tools/subprocess_utils.py`.
- `run_command_safe` policy enforcement lives in `app/tools/policies/run_command_safe.py`.
- Git operations should always use the specialized `git_*` tools instead of shelling out through command tools.
