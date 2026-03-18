# ToolRunner

`toolrunner.app.main` exposes a signed execution API for workspace-safe tool calls plus the local orchestration UI. This document is contract-first: the JSON shown here matches the current Pydantic request models and the result payloads returned by the handlers in `toolrunner/app/tools/`.

## Smoke Testing

The ToolRunner smoke-test runbook lives in:

- `docs/smoke/toolrunner-smoke-plan.md`

Use that document for focused ToolRunner verification instead of older root-level smoke doc paths.

## Execute API

Primary route:

- `POST /v1/run/tool`

Legacy alias:

- `POST /v1/execute`

Requests must be JSON and signed with:

- `X-AM-Timestamp`
- `X-AM-Signature`

The signature verifier currently expects the same HMAC flow covered by `toolrunner/app/tests/test_auth.py`.

### Execute request envelope

Every tool call goes through the same outer request shape:

```json
{
  "request_id": "req-123",
  "workspace_id": "ws-1",
  "run_id": "run-1",
  "tool_name": "run_command",
  "args": {
    "cmd": ["python", "-c", "print('ok')"],
    "cwd": "."
  },
  "policy": {
    "allow_write": false,
    "allowed_roots": []
  },
  "limits": {
    "timeout_s": 30,
    "max_output_bytes": 4096
  }
}
```

Field notes:

- `request_id`, `workspace_id`, `run_id`, and `tool_name` are required non-empty strings.
- `args` is tool-specific and is validated against the Pydantic args model for `tool_name`.
- `policy` is optional and is passed through to handlers that enforce path/write policy.
- `limits.timeout_s` and `limits.max_output_bytes` are part of the outer envelope, but only `shell_exec` and `python_exec` consume them directly.

### Execute success envelope

The outer response is always an `ExecuteResponse`:

```json
{
  "request_id": "req-123",
  "status": "COMPLETED",
  "exit_code": 0,
  "stdout": "ok\n",
  "stderr": "",
  "duration_ms": 18,
  "result": {
    "tool": "run_command",
    "policy": {
      "allow_write": false,
      "allowed_roots": []
    },
    "tool_result": {
      "ok": true,
      "result": {
        "exit_code": 0,
        "duration_ms": 17,
        "timed_out": false,
        "stdout": "ok\n",
        "stderr": "",
        "stdout_truncated": false,
        "stderr_truncated": false,
        "timeout_ms": 300000,
        "timeout_seconds": 300.0,
        "timeout_source": "args.timeout_ms"
      }
    }
  }
}
```

Field notes:

- `status` is `COMPLETED` or `FAILED`.
- `result.tool_result` is the actual tool handler payload.
- `stdout`, `stderr`, and `exit_code` on the outer envelope are mirrored from the nested tool result when the handler exposes them.
- The outer status is computed from execution outcome, not just nested `tool_result.ok`.

### Execute validation / request errors

Invalid JSON or invalid outer request bodies return a plain error envelope instead of `ExecuteResponse`:

```json
{
  "ok": false,
  "error": {
    "code": "tool_runner.INVALID_REQUEST",
    "message": "request validation failed",
    "details": {
      "errors": [],
      "tool": "run_command",
      "request_id": "req-123"
    }
  }
}
```

## Webhook Endpoint

`webhook` is a dedicated signed endpoint and does not use the normal `/v1/run/tool` execute envelope.

Primary route:

- `POST /v1/run/tool/webhook`

Legacy alias:

- `POST /v1/webhook`

Requests must still be JSON and signed with:

- `X-AM-Timestamp`
- `X-AM-Signature`

Request body:

```json
{
  "event": "smoke-webhook",
  "run_id": "smoke-webhook-run",
  "payload": {
    "source": "smoke"
  }
}
```

Field notes:

- `event` is required and must be a non-empty string.
- `run_id` is optional and defaults to `"unknown"` when omitted.
- `payload` is optional and, when present, must be a JSON object.

Success response:

```json
{
  "ok": true,
  "result": {
    "accepted": true,
    "tool": "webhook",
    "event": "smoke-webhook",
    "run_id": "smoke-webhook-run",
    "payload": {
      "source": "smoke"
    }
  }
}
```

Validation failures return normal FastAPI HTTP errors for this endpoint, for example:

- `400 {"detail": "event required"}`
- `400 {"detail": "payload must be an object"}`

## Timeout And Output Limit Rules

Timeouts and output caps are no longer uniform across all tools. The active source depends on the tool family.

### 1. Outer execute limits

The outer execute envelope contains:

```json
{
  "limits": {
    "timeout_s": 30,
    "max_output_bytes": 4096
  }
}
```

These are used by:

- `shell_exec`
- `python_exec`

They are not used by `run_command`, `test_runner`, `format_runner`, `coverage_runner`, `lint_runner`, `typecheck_runner`, `search_code`, or the git tools.

### 2. Tool-level timeout fields

These tools carry their own timeout and output fields inside `args`:

- `search_code`: `timeout_ms`
- `run_command`: `timeout_ms`, `max_output_bytes`
- `test_runner`: `timeout_ms`, `max_output_bytes`
- `format_runner`: `timeout_ms`, `max_output_bytes`
- `coverage_runner`: `timeout_ms`, `max_output_bytes`
- `lint_runner`: `timeout_ms`, `max_output_bytes`
- `typecheck_runner`: `timeout_ms`, `max_output_bytes`
- `git_status`: `timeout_ms`, `max_output_bytes`
- `git_diff`: `timeout_ms`, `max_output_bytes`
- `git_branch_create`: `timeout_ms`, `max_output_bytes`
- `git_add`: `timeout_ms`, `max_output_bytes`
- `git_push`: `timeout_ms`, `max_output_bytes`
- `git_checkout`: `timeout_ms`, `max_output_bytes`
- `git_commit`: `timeout_ms`, `max_output_bytes`
- `git_apply`: `timeout_ms`, `max_output_bytes`

`git_log` is the exception in the git family: its args model does not expose timeout or output fields, so it currently inherits the defaults from `RunCommandArgs` when it shells out internally.

### 3. Model defaults

If a tool-specific timeout/output field is omitted, the args model default applies. Current defaults in code include:

- `search_code.timeout_ms = 3000`
- `run_command.timeout_ms = 300000`, `max_output_bytes = 262144`
- `test_runner.timeout_ms = 600000`, `max_output_bytes = 524288`
- `format_runner.timeout_ms = 180000`, `max_output_bytes = 262144`
- `coverage_runner.timeout_ms = 600000`, `max_output_bytes = 524288`
- `lint_runner.timeout_ms = 180000`, `max_output_bytes = 262144`
- `typecheck_runner.timeout_ms = 300000`, `max_output_bytes = 262144`
- most configurable git tools default to `timeout_ms = 60000` or lower and `max_output_bytes = 262144`

### 4. Orchestrator clamp precedence

When the orchestrator sends a tool call, it can clamp tool args before the request reaches the API:

1. If `ToolCall.timeout_ms_override` or `ToolCall.max_output_bytes_override` is present, that value is used as the requested value.
2. Otherwise the orchestrator uses the tool args value already present on the call.
3. Otherwise it falls back to the tool-call defaults.
4. The final value is clamped to the global caps:
   - `DEFAULT_CALL_TIMEOUT_MS = COMMAND_TIMEOUT * 1000`
   - `DEFAULT_CALL_OUTPUT_BYTES = OUTPUT_LIMIT`

This means the effective timeout/output for orchestrated tool calls is:

- not greater than the orchestrator cap
- optionally smaller if an override is provided
- otherwise the smaller of the requested tool arg and the global cap

Direct callers to `/v1/run/tool` bypass the orchestrator clamp.

### 5. Timeout behavior by tool family

- `run_command` returns `ok: true` with `result.timed_out: true`; the execute wrapper marks the outer status as `FAILED`.
- `test_runner`, `format_runner`, and `coverage_runner` convert timeouts into `ok: false` error envelopes and include timeout metadata in `error.details`.
- `search_code` does not return `tool_runner.TIMED_OUT`; when its deadline expires it returns `ok: true` with `result.truncated: true`.
- `shell_exec` and `python_exec` return a normalized command result. A timeout produces `tool_runner.TIMED_OUT`.
- For tools that call `run_command`, a `timeout_source` field is included in the result or error details. Today that source is usually `args.timeout_ms` or `limits.timeout_s`.

### 6. Zero means "no timeout" only for `timeout_ms`

- Tools with `timeout_ms` allow `0`, and the implementation treats that as no timeout.
- `search_code.timeout_ms = 0` disables the scan deadline.
- The outer `limits.timeout_s` field does not allow `0`; `shell_exec` and `python_exec` always require a positive timeout.

## Tool Contracts

Unless stated otherwise, the tool payload shown below is the nested `result.tool_result` value returned by the handler and embedded under the outer execute response.

### `file_read`

Request args:

```json
{
  "path": "docs/toolrunner.md",
  "mode": "text",
  "encoding": "utf-8",
  "start_line": 1,
  "end_line": 40,
  "max_bytes": 262144,
  "absolute_root": null
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "path": "docs/toolrunner.md",
    "mode": "text",
    "encoding": "utf-8",
    "content": "# ToolRunner\n",
    "start_line": 1,
    "end_line": 40,
    "total_lines": 40,
    "truncated": false
  },
  "meta": {
    "policy": {}
  }
}
```

Binary mode returns `content_base64` and `byte_length` instead of text fields.

### `file_write`

Request args:

```json
{
  "path": "tmp/output.txt",
  "mode": "text",
  "content": "hello\n",
  "content_base64": null,
  "encoding": "utf-8",
  "overwrite": false,
  "make_dirs": true,
  "atomic": true,
  "expected_sha256": null
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "path": "tmp/output.txt",
    "bytes_written": 6,
    "sha256": "abc123",
    "resolved_path": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run/tmp/output.txt",
    "created": true,
    "overwritten": false
  },
  "meta": {
    "policy": {},
    "sandbox": {
      "workspace_dir": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run",
      "sandbox_root": "C:/Dev/AgentMaestro/toolrunner/sandbox",
      "resolved_path": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run/tmp/output.txt"
    }
  }
}
```

### `file_delete`

Request args:

```json
{
  "path": "tmp/output.txt",
  "recursive": false,
  "missing_ok": false
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "path": "tmp/output.txt",
    "resolved_path": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run/tmp/output.txt",
    "deleted": true,
    "missing": false,
    "deleted_type": "file"
  },
  "meta": {
    "policy": {}
  }
}
```

When `missing_ok=true` and the target is absent, the tool still returns `ok: true` with `deleted: false` and `missing: true`.

### `file_patch`

Request args:

```json
{
  "path": "app/example.py",
  "patch_unified": "--- a/app/example.py\n+++ b/app/example.py\n@@ -1,1 +1,1 @@\n-print('old')\n+print('new')\n",
  "strip_prefix": 0,
  "fail_on_reject": true,
  "expected_sha256": null,
  "create_if_missing": false,
  "backup": true
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "path": "app/example.py",
    "applied": true,
    "applied_partially": false,
    "hunks_total": 1,
    "hunks_applied": 1,
    "hunks_failed": 0,
    "failed_hunks": [],
    "sha256_before": "before",
    "sha256_after": "after",
    "backup_path": "C:/Dev/AgentMaestro/.../.toolrunner_backups/app/example.py.20260311T120000Z.bak",
    "rejects_path": null
  },
  "meta": {
    "policy": {}
  }
}
```

Notes:

- `strip_prefix=0` means "auto-detect when possible."
- Partial applies keep `ok: true` and set `applied_partially: true` when `fail_on_reject=false`.
- Patch parse or hunk failures return `tool_runner.PATCH_FAILED`.

### `repo_tree`

Request args:

```json
{
  "path": ".",
  "max_depth": 6,
  "include_files": true,
  "include_dirs": true,
  "follow_symlinks": false,
  "exclude_globs": ["**/.git/**", "**/.venv/**", "**/node_modules/**", "**/__pycache__/**"],
  "include_globs": null,
  "max_entries": 5000,
  "include_metadata": true,
  "absolute_root": null
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "root": ".",
    "max_depth": 6,
    "truncated": false,
    "entries": [
      {
        "type": "file",
        "path": "docs/toolrunner.md",
        "depth": 1,
        "size_bytes": 1234,
        "mtime_epoch": 1773240000
      }
    ],
    "stats": {
      "files": 1,
      "dirs": 0,
      "entries": 1,
      "excluded": 0,
      "excluded_patterns": [],
      "excluded_matches_by_pattern": {},
      "allowed_roots": []
    }
  },
  "meta": {
    "policy": {}
  }
}
```

### `search_code`

Request args:

```json
{
  "query": "timeout_source",
  "is_regex": false,
  "case_sensitive": false,
  "root": ".",
  "absolute_root": null,
  "include_globs": ["**/*.py"],
  "exclude_globs": ["**/.git/**", "**/.venv/**", "**/node_modules/**"],
  "max_results": 100,
  "max_matches_per_file": 20,
  "context_lines": 2,
  "timeout_ms": 3000
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "query": "timeout_source",
    "is_regex": false,
    "case_sensitive": false,
    "truncated": false,
    "matches": [
      {
        "path": "toolrunner/app/tools/run_command.py",
        "match_count": 2,
        "snippets": [
          {
            "line": 34,
            "col": 20,
            "line_text": "        return {\"timeout_ms\": None, \"timeout_seconds\": None, \"timeout_source\": source}",
            "context_before": [],
            "context_after": []
          }
        ]
      }
    ],
    "stats": {
      "files_scanned": 1,
      "files_with_matches": 1,
      "total_matches": 2,
      "excluded": 0,
      "excluded_patterns": [],
      "excluded_matches_by_pattern": {},
      "allowed_roots": []
    }
  },
  "meta": {
    "policy": {}
  }
}
```

## Command Execution Tools

### `shell_exec`

Request args:

```json
{
  "cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"],
  "cwd": ".",
  "env": {}
}
```

The handler uses the outer execute limits, not `args.timeout_ms`.

Success result:

```json
{
  "ok": true,
  "result": {
    "command": ["powershell", "-NoProfile", "-Command", "Get-Location"],
    "cwd": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run",
    "exit_code": 0,
    "timed_out": false,
    "stdout": "Path\n----\n...",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "timeout_value": 30,
    "timeout_unit": "seconds",
    "timeout_source": "limits.timeout_s"
  }
}
```

### `python_exec`

Request args:

```json
{
  "code": "print('ok')",
  "files": [],
  "entrypoint": null
}
```

Success result matches the same normalized command shape as `shell_exec`.

If `entrypoint` is used, `files` must also be provided so the entrypoint exists under the run directory.

### `run_command`

Request args:

```json
{
  "cmd": ["python", "-c", "print('ok')"],
  "cwd": ".",
  "env": {},
  "timeout_ms": 300000,
  "max_output_bytes": 262144,
  "stdin_text": null
}
```

Direct handler success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 0,
    "duration_ms": 18,
    "timed_out": false,
    "stdout": "ok\n",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "timeout_ms": 300000,
    "timeout_seconds": 300.0,
    "timeout_source": "args.timeout_ms"
  }
}
```

Notes:

- `timeout_ms=0` disables the timeout.
- On timeout, the tool still returns `ok: true` with `timed_out: true`; the outer execute response becomes `FAILED`.
- On Windows, timeouts terminate the process tree with `taskkill /T /F`.

## Test And Quality Tools

### `test_runner`

Request args:

```json
{
  "kind": "powershell_script",
  "script_path": "scripts/test.ps1",
  "script_args": ["-q"],
  "pytest_args": null,
  "cmd": null,
  "cwd": ".",
  "env": {},
  "timeout_ms": 600000,
  "max_output_bytes": 524288,
  "parse": "pytest"
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 0,
    "duration_ms": 1200,
    "timed_out": false,
    "summary": {
      "passed": 10,
      "failed": 0,
      "skipped": 0,
      "xfailed": 0,
      "xpassed": 0,
      "errors": 0
    },
    "parse_mode": "pytest",
    "failed_tests": [],
    "stdout": "",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false
  }
}
```

Failure/timeouts return `ok: false` with a `result` object shaped like the success result plus `error.code` such as:

- `tool_runner.TIMED_OUT`
- `tool_runner.TESTS_FAILED`

### `format_runner`

Request args:

```json
{
  "tool": "ruff_format",
  "mode": "check",
  "cwd": ".",
  "paths": ["toolrunner/app"],
  "args": null,
  "cmd": null,
  "timeout_ms": 180000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 0,
    "duration_ms": 400,
    "timed_out": false,
    "changed_files": [],
    "parse_mode": "ruff_format",
    "parse_warning": null,
    "stdout": "",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false
  }
}
```

Notes:

- `tool="command"` requires `cmd` and bypasses the built-in formatter command construction.
- For `ruff_format`, `mode=check` injects `--check --diff`; `mode=apply` injects `--diff`.
- Timeout failures return `tool_runner.TIMED_OUT`.

### `coverage_runner`

Request args:

```json
{
  "kind": "pytest_coverage",
  "cwd": ".",
  "args": ["toolrunner/app/tests/test_run_command.py"],
  "timeout_ms": 600000,
  "max_output_bytes": 524288
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 0,
    "duration_ms": 1200,
    "timed_out": false,
    "total_percent": 92.5,
    "files": [
      {
        "path": "toolrunner/app/tools/run_command.py",
        "percent": 95.0
      }
    ],
    "stdout": "",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "coverage_stdout": "",
    "coverage_stderr": "",
    "coverage_duration_ms": 200,
    "coverage_json_path": "C:/Dev/AgentMaestro/toolrunner/sandbox/ws/run/coverage.json"
  }
}
```

### `lint_runner`

Request args:

```json
{
  "tool": "ruff",
  "cwd": ".",
  "paths": ["toolrunner/app"],
  "args": null,
  "cmd": null,
  "timeout_ms": 180000,
  "max_output_bytes": 262144,
  "parse": "ruff"
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 1,
    "duration_ms": 350,
    "timed_out": false,
    "issues": [
      {
        "path": "toolrunner/app/main.py",
        "line": 10,
        "col": 1,
        "code": "F401",
        "severity": "error",
        "message": "unused import"
      }
    ],
    "stdout": "[]",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "parse_mode": "ruff",
    "parse_source": "stdout",
    "parse_warning": null
  }
}
```

The linter result stays `ok: true` even when the linter exits non-zero; callers should inspect `exit_code`, `issues`, and `timed_out`.

### `typecheck_runner`

Request args:

```json
{
  "tool": "pyright",
  "cwd": ".",
  "args": null,
  "cmd": null,
  "timeout_ms": 300000,
  "max_output_bytes": 262144,
  "parse": "pyright"
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "exit_code": 1,
    "duration_ms": 500,
    "timed_out": false,
    "diagnostics": [
      {
        "path": "toolrunner/app/main.py",
        "line": 12,
        "col": 4,
        "severity": "error",
        "code": "reportGeneralTypeIssues",
        "message": "example diagnostic"
      }
    ],
    "stdout": "{}",
    "stderr": "",
    "stdout_truncated": false,
    "stderr_truncated": false,
    "parse_mode": "pyright",
    "parse_source": "stdout",
    "parse_warning": null
  }
}
```

Like `lint_runner`, this tool keeps `ok: true` for normal checker failures and expects callers to inspect `exit_code`, `diagnostics`, and `timed_out`.

## Git Tools

### `git_status`

Request args:

```json
{
  "repo_dir": ".",
  "porcelain": "v2",
  "include_untracked": true,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "branch": {
      "name": "main",
      "head_oid": "abc123",
      "upstream": "origin/main",
      "ahead": 0,
      "behind": 0,
      "detached": false
    },
    "is_clean": true,
    "staged": [],
    "unstaged": [],
    "untracked": [],
    "conflicts": [],
    "raw": {
      "stdout": "",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

### `git_diff`

Request args:

```json
{
  "repo_dir": ".",
  "staged": false,
  "paths": ["toolrunner/app/main.py"],
  "context_lines": 3,
  "detect_renames": true,
  "timeout_ms": 60000,
  "max_output_bytes": 524288
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "staged": false,
    "paths": ["toolrunner/app/main.py"],
    "diff": "diff --git ...",
    "truncated": false,
    "raw": {
      "stdout": "diff --git ...",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

### `git_branch_create`

Request args:

```json
{
  "repo_dir": ".",
  "name": "agent/docs-sync",
  "start_point": "HEAD",
  "checkout": true,
  "force": false,
  "timeout_ms": 120000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "name": "agent/docs-sync",
    "checked_out": true
  }
}
```

### `git_add`

Request args:

```json
{
  "repo_dir": ".",
  "paths": ["docs/toolrunner.md"],
  "all": false,
  "intent_to_add": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "staged_paths": ["docs/toolrunner.md"],
    "raw": {
      "stdout": "",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

### `git_checkout`

Request args:

```json
{
  "repo_dir": ".",
  "ref": "main",
  "create": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "ref": "main",
    "detached": false,
    "raw": {
      "stdout": "Already on 'main'\n",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

### `git_commit`

Request args:

```json
{
  "repo_dir": ".",
  "message": "Update ToolRunner docs",
  "paths_to_add": ["docs/toolrunner.md"],
  "add_all": false,
  "signoff": false,
  "amend": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "commit_oid": "abc123",
    "summary": "Update ToolRunner docs",
    "changed_files": 1,
    "changed_files_truncated": false,
    "raw": {
      "stdout": "[main abc123] Update ToolRunner docs\n",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

The commit handler performs additional validation and can return:

- `tool_runner.CONFLICT` for "nothing to commit"
- `tool_runner.INVALID_ARGUMENT` for invalid git output / add / commit failures

### `git_log`

Request args:

```json
{
  "repo_dir": ".",
  "max_count": 20,
  "ref": "HEAD"
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "ref": "HEAD",
    "max_count": 20,
    "commits": [
      {
        "oid": "abc123",
        "author_name": "Scott",
        "author_email": "scott@example.com",
        "author_time_epoch": 1773240000,
        "subject": "Update ToolRunner docs"
      }
    ],
    "raw": {
      "stdout": "abc123\u0000Scott\u0000scott@example.com\u00001773240000\u0000Update ToolRunner docs\n",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

`git_log` currently uses the internal `RunCommandArgs` defaults for timeout/output because its public args model does not expose those fields.

### `git_apply`

Request args:

```json
{
  "repo_dir": ".",
  "patch_unified": "--- a/docs/toolrunner.md\n+++ b/docs/toolrunner.md\n@@ -1,1 +1,1 @@\n-old\n+new\n",
  "strip_prefix": 1,
  "reject": true,
  "check": false,
  "include_untracked": true,
  "timeout_ms": 30000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "strip_prefix": 1,
    "check_passed": null,
    "applied": true,
    "rejects_created": false,
    "reject_paths": [],
    "raw": {
      "stdout": "",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

Note: `GitApplyArgs` currently defines `timeout_ms` and `max_output_bytes` twice in the model, and the later definitions win. The effective defaults are `timeout_ms = 30000` and `max_output_bytes = 262144`.

### `git_push`

Request args:

```json
{
  "repo_dir": ".",
  "remote": "origin",
  "ref": "main",
  "set_upstream": true,
  "force": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

Success result:

```json
{
  "ok": true,
  "result": {
    "repo_dir": ".",
    "remote": "origin",
    "ref": "main",
    "pushed": true,
    "raw": {
      "stdout": "",
      "stderr": "",
      "stdout_truncated": false,
      "stderr_truncated": false
    }
  }
}
```

## Orchestrator UI Dashboard

The FastAPI `toolrunner.app.main` module also serves a `/ui` dashboard that mirrors the Maestro/Apprentice workflow:

- User tab: A chat-first experience where Maestro asks clarifying questions, drafts sections, and locks them conversationally. Each turn is appended to `.agentmaestro/runs/<run_id>/chat/transcript.jsonl`, updates the preview/completeness widgets, and still flows through `SRS.md` and `SRS.lock.json` while emitting `CHAT_MESSAGE`, `SRS_UPDATED`, and `SRS_SECTION_LOCKED` events.
- Maestro tab: Displays the latest synthesized plan summary, the SRS readiness score and missing items, and raw JSON while letting you regenerate a schema-valid `plan.json` via `/v1/runs/{run_id}/plan/generate`.
- Apprentice tab: Shows start/stop controls plus an event feed that polls `/v1/runs/{run_id}/events`, reflecting SRS events, approvals, plan generation, and orchestrator activity.

Run artifacts are persisted per run:

- `charter.json`, `plans/<plan_id>.json`, `plans/latest.json`, `step_reports/<milestone_id>/<step_id>.json`
- `events.jsonl`
- `chat/transcript.jsonl`
- `srs/SRS.md` and `srs/SRS.lock.json`
- `srs/readiness.json`
- `approvals.json`

### Trivial Trial button and API

- The User tab displays a `Trivial Trial (Seed + Plan + Run)` button above the chat area.
- The button calls `POST /v1/trials/trivial` and can accept JSON or form data.
- Defaults: `repo_dir="."`, a `slug` normalized to start with `trial-`, `start=true`, `override_readiness=true`, and `template="todo_cli_v1"`.

Request example:

```json
{
  "repo_dir": ".",
  "slug": "trivial-trial",
  "start": true,
  "override_readiness": true,
  "template": "todo_cli_v1"
}
```

Response example:

```json
{
  "ok": true,
  "run_id": "trial-trivial-xxxxxxxx",
  "paths": {
    "run_dir": ".agentmaestro/runs/trial-trivial-xxxxxxxx",
    "srs_md": ".agentmaestro/runs/trial-trivial-xxxxxxxx/srs/SRS.md",
    "plan_latest": ".agentmaestro/runs/trial-trivial-xxxxxxxx/plans/latest.json"
  },
  "seeded_sections": [
    "project_summary",
    "goals_non_goals",
    "functional_requirements",
    "interfaces",
    "acceptance_criteria",
    "risks_assumptions"
  ],
  "plan_generated": true,
  "run_started": true
}
```

Key API routes for dashboard workflows:

- `POST /v1/runs`
- `GET /v1/runs/{run_id}`
- `POST /v1/runs/{run_id}/start`
- `POST /v1/runs/{run_id}/stop`
- `GET /v1/runs/{run_id}/events?since=<id>`
- `GET /v1/runs/{run_id}/srs/...`
- `GET /v1/runs/{run_id}/srs/readiness`
- `POST /v1/runs/{run_id}/srs/...`
- `POST /v1/runs/{run_id}/plan/generate`
- `GET /v1/runs/{run_id}/plan`
- `POST /v1/runs/{run_id}/approve`
- `GET /v1/runs/{run_id}/step_reports`
- `GET /v1/runs/{run_id}/step_reports/{milestone_id}/{step_id}`
- `POST /v1/runs/{run_id}/chat`
- `GET /v1/runs/{run_id}/chat/history`
- `POST /v1/runs/{run_id}/chat/reset`

### Running the UI

1. Ensure the FastAPI app is running, for example `uvicorn toolrunner.app.main:app --reload` from `toolrunner/`.
2. Visit `http://localhost:8000/ui`.
3. Use the User tab to draft or lock sections, the Maestro tab to generate plans, and the Apprentice tab to watch events.
