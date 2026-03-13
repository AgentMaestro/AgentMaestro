# ToolRunner Remaining Smoke Plan

This document covers the remaining ToolRunner smoke coverage after the first 11 tools were agent-smoked successfully.

Current status:

- Completed: `file_read`, `file_write`, `repo_tree`, `file_patch`, `test_runner`, `shell_exec`, `python_exec`, `format_runner`, `coverage_runner`, `file_delete`, `run_command`
- Complete count: `24 / 24`
- Remaining count: `0 / 24`

Remaining tools in scope:

- `lint_runner` - PASSED
- `typecheck_runner` - PASSED
- `search_code` - PASSED
- `webhook` - PASSED
- `git_status` - PASSED
- `git_add` - PASSED
- `git_commit` - PASSED
- `git_push` - PASSED
- `git_diff` - PASSED
- `git_log` - PASSED
- `git_apply` - PASSED
- `git_branch_create` - PASSED
- `git_checkout` - PASSED

## Strategy

Use the already-smoked tools only for setup and verification. Each remaining tool should still be exercised directly and should produce evidence that its own JSON response shape and side effects are correct.

Recommended sequencing:

1. Read-only analysis tools: `search_code`, `lint_runner`, `typecheck_runner`
2. Special-case endpoint: `webhook`
3. Git wave 1: `git_status`, `git_add`, `git_commit`, `git_push`
4. Git wave 2: `git_diff`, `git_log`, `git_apply`, `git_branch_create`, `git_checkout`

Why this order:

- The read-only tools are low-risk and easy to validate first.
- `webhook` is a routing/contract special case and should be isolated.
- Git wave 1 establishes a disposable repo with clean, reproducible history.
- Git wave 2 depends on that disposable repo already having commits, branches, and patchable content.

## Execution Rules

- Use a disposable repo or sandbox path only. Do not touch the real project git history.
- Prefer one disposable repo root such as `smoke/git-wave1` and `smoke/git-wave2`.
- Use local bare remotes for `git_push`. Do not push to `origin` or any network remote.
- Reuse already-smoked tools for setup:
  - `file_write` to create fixture files
  - `file_read` to confirm file content
  - `run_command` or `shell_exec` to initialize repos, configure git identity, or inspect the bare remote
- Keep each smoke focused: one primary target tool per prompt.
- Finish each run with a short PASS/FAIL summary that includes the target tool response fields that prove the tool worked.

## Success Criteria Per Tool

- `search_code`: *PASSED* returns at least one match with correct snippet metadata and sane `stats`
- `lint_runner`: *PASSED* returns structured `issues` and non-empty `parse_mode`
- `typecheck_runner`: *PASSED* returns structured `diagnostics` and non-empty `parse_mode`
- `webhook`: *PASSED* direct endpoint accepts payload with `event` and returns `ok=true` plus a stable `result` echo contract
- `git_status`: *PASSED* returns branch/cleanliness/staged/unstaged/untracked structure
- `git_add`: *PASSED* stages expected path and returns `staged_paths`
- `git_commit`: *PASSED* creates a commit and returns `commit_oid` and `summary`
- `git_push`: *PASSED* pushes to a local bare remote and returns `pushed=true`
- `git_diff`: *PASSED* returns unified diff text for a known unstaged or staged edit
- `git_log`: *PASSED* returns structured commit metadata including `author_time_iso`, plus `parse_stats` and any parse warnings when applicable
- `git_apply`: *PASSED* applies a known patch and reports `applied=true` or `check_passed=true`, plus `touched_paths`
- `git_branch_create`: *PASSED* creates a branch and reports `checked_out` accurately; timeout failures explicitly return `tool_runner.TIMED_OUT` with `error.details.timed_out=true`
- `git_checkout`: *PASSED* switches ref/branch and reports `detached` accurately

## Current JSON Response Shapes

These are the current success-path response shapes for the remaining-smoke tools. Normal tool calls return `{"ok": true, "result": {...}}`. Error cases return `{"ok": false, "error": {"code": "tool_runner.*", "message": "...", "details": {...}}}`.

- `search_code`: `result.query`, `result.is_regex`, `result.case_sensitive`, `result.matches[]`, `result.stats`, `result.truncated`
- `lint_runner`: `result.exit_code`, `result.duration_ms`, `result.timed_out`, `result.issues[]`, `result.parse_mode`, `result.parse_source`, `result.parse_warning`, `result.stdout`, `result.stderr`
- `typecheck_runner`: `result.exit_code`, `result.duration_ms`, `result.timed_out`, `result.diagnostics[]`, `result.parse_mode`, `result.parse_source`, `result.parse_warning`, `result.stdout`, `result.stderr`
- `webhook`: `result.accepted`, `result.tool`, `result.event`, `result.run_id`, `result.payload`
- `git_status`: `result.repo_dir`, `result.branch`, `result.is_clean`, `result.staged[]`, `result.unstaged[]`, `result.untracked[]`, `result.conflicts[]`, `result.raw`
- `git_add`: `result.repo_dir`, `result.staged_paths[]`, `result.raw`
- `git_commit`: `result.repo_dir`, `result.commit_oid`, `result.summary`, `result.changed_files`, `result.changed_files_truncated`, `result.raw`
- `git_push`: `result.repo_dir`, `result.remote`, `result.ref`, `result.pushed`, `result.raw`
- `git_diff`: `result.repo_dir`, `result.staged`, `result.paths`, `result.diff`, `result.truncated`, `result.raw`
- `git_log`: `result.repo_dir`, `result.ref`, `result.max_count`, `result.commits[]`, `result.parse_stats`, optional `result.parse_warning`, `result.raw`
  - each commit includes `oid`, `author_name`, `author_email`, `author_time_epoch`, `author_time_iso`, and `subject`
- `git_apply`: `result.repo_dir`, `result.strip_prefix`, `result.check_passed`, `result.applied`, `result.touched_paths[]`, `result.rejects_created`, `result.reject_paths[]`, `result.raw`
- `git_branch_create`: success shape is `result.repo_dir`, `result.name`, `result.checked_out`
  - timeout failures return `tool_runner.TIMED_OUT` with `error.details.phase`, `error.details.timed_out=true`, `error.details.timeout_ms`, and `error.details.timeout_source`
- `git_checkout`: `result.repo_dir`, `result.ref`, `result.detached`, `result.raw`

## Special Handling: `webhook`

`webhook` is not a normal `_TOOL_HANDLERS` entry in `toolrunner/app/main.py`. It is exposed through:

- `POST /v1/run/tool/webhook`
- `POST /v1/webhook`

That means webhook smoke should be treated as a direct HTTP endpoint smoke, not a standard tool-call smoke through `/v1/run/tool`.

Important constraints:

- Do not guess the base URL. Read the configured ToolRunner base URL from local project config or environment first.
- In local dev this is typically `http://127.0.0.1:8001`, but the smoke must use the configured value, not a hard-coded assumption.
- These webhook routes are signed. A plain unsigned POST is not a valid smoke.
- The request must include:
  - `X-AM-Timestamp`
  - `X-AM-Signature`
- The signature format is the same as the rest of ToolRunner: `HMAC_SHA256(secret, f"{timestamp}.{body}")` using `TOOLRUNNER_SECRET`.
- If the server accepts TCP on the port but does not return an HTTP response, treat that as a ToolRunner runtime failure or hung server, not as a webhook contract mismatch.

Use the dedicated local smoke harness at `toolrunner/scripts/webhook_smoke.py`.

For agent-driven smoke, do not inline a Python `urllib` POST through `run_command`. Instead:

- use `run_command` to start `python toolrunner/scripts/webhook_smoke.py start`
- capture the printed `result_path`
- remember that `start` is detached and returns before the HTTP request completes
- wait briefly, then use `file_read` or another read-safe tool to inspect that result JSON
- if needed, re-read the same file until `state` becomes `completed`

Smoke this against the dedicated webhook contract documented in `docs/toolrunner.md`, not against the normal `ExecuteResponse` envelope used by `/v1/run/tool`.

## Common Prompt Wrapper

Use this wrapper pattern for every prompt:

1. Create or reuse a disposable sandbox area under `smoke/`.
2. Use already-smoked tools only for setup and verification.
3. Call the target tool directly.
4. Return a compact final report in exactly this format:

```text
STATUS: PASS or FAIL
TOOL: <tool_name>
EVIDENCE: <one-sentence proof using response fields or side effects>
DETAILS: <key JSON fields or mismatch>
```

## Ready Prompts

### Prompt: `search_code`

```text
Create a disposable search fixture under `smoke/search-code/` using file_write. Add at least two Python files and one text file. Put the string `agent smoke needle` in exactly two Python files.

Then call `search_code` with:
- `query='agent smoke needle'`
- `is_regex=false`
- `case_sensitive=false`
- `root='smoke/search-code'`
- `include_globs=['**/*.py']`
- `max_results=10`

Verify that:
- `matches` contains the two Python files
- each match has snippet metadata with `line`, `col`, and `line_text`
- `stats.files_scanned` is greater than zero

Return only:
STATUS: PASS or FAIL
TOOL: search_code
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `lint_runner`

```text
Create a disposable Python file at `smoke/lint-runner/bad_lint.py` using file_write. Make it contain at least one obvious Ruff issue, such as an unused import.

Then call `lint_runner` with:
- `tool='ruff'`
- `cwd='.'`
- `paths=['smoke/lint-runner']`

Verify that:
- the response contains `issues`
- at least one issue points at `smoke/lint-runner/bad_lint.py`
- `parse_mode` is `ruff`

Do not fix the file. Return only:
STATUS: PASS or FAIL
TOOL: lint_runner
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `typecheck_runner`

```text
Create a disposable Python file at `smoke/typecheck-runner/bad_types.py` using file_write. Make it contain at least one obvious type problem that Mypy should report, such as assigning a string to an integer-typed variable.

Then call `typecheck_runner` with:
- `tool='mypy'`
- `cwd='.'`
- `args=['smoke/typecheck-runner/bad_types.py']`

Verify that:
- the response contains `diagnostics`
- at least one diagnostic points at `smoke/typecheck-runner/bad_types.py`
- `parse_mode` is `mypy`

Do not fix the file. Return only:
STATUS: PASS or FAIL
TOOL: typecheck_runner
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `webhook`

```text
Treat this as a direct webhook endpoint smoke, not a standard `/v1/run/tool` tool-call smoke.

Use the dedicated harness instead of an inline Python HTTP snippet.

1. Start the harness with `run_command`:
   - `python toolrunner/scripts/webhook_smoke.py start`
2. Capture the printed `result_path` from stdout.
3. Wait briefly because the worker is detached.
4. Read that JSON file with `file_read`.
5. If the JSON `state` is not yet `completed`, wait and read the same file again.
6. Verify from the captured result JSON that:
   - the endpoint accepted the payload
   - `ok` is `true`
   - `result.accepted` is `true`
   - `result.event` echoes `smoke-webhook`
   - `result.run_id` echoes `smoke-webhook-run`

The harness itself is responsible for:
- determining the configured ToolRunner base URL
- signing the request with `TOOLRUNNER_SECRET`
- sending the POST to `/v1/run/tool/webhook`
- capturing raw HTTP response data or timeout/error details

If the result JSON shows no HTTP response because the ToolRunner server hangs or times out, report FAIL and explicitly say it is a runtime/server-availability problem rather than a webhook contract mismatch.

Return only:
STATUS: PASS or FAIL
TOOL: webhook
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_status`

```text
Create a disposable git repo under `smoke/git-wave1/repo` using `run_command`. Initialize git, configure `user.name` and `user.email`, and create one tracked file plus one untracked file using file_write.

Then call `git_status` with:
- `repo_dir='smoke/git-wave1/repo'`
- `porcelain='v2'`
- `include_untracked=true`

Verify that:
- `branch.name` is present
- `untracked` includes the untracked file
- `is_clean` is false

Return only:
STATUS: PASS or FAIL
TOOL: git_status
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_add`

```text
Reuse or create a disposable repo under `smoke/git-wave1/repo`. Ensure there is an untracked file at `notes.txt`.

Then call `git_add` with:
- `repo_dir='smoke/git-wave1/repo'`
- `paths=['notes.txt']`

After that, call `git_status` to verify the file is now staged.

Smoke success requires:
- `git_add.result.staged_paths` includes `notes.txt`
- follow-up `git_status` shows `notes.txt` in `staged`

Return only:
STATUS: PASS or FAIL
TOOL: git_add
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_commit`

```text
Reuse or create a disposable repo under `smoke/git-wave1/repo`. Ensure there is at least one staged file ready to commit.

Then call `git_commit` with:
- `repo_dir='smoke/git-wave1/repo'`
- `message='Smoke commit for git_commit tool'`

Verify that:
- the response includes a non-empty `commit_oid`
- the response `summary` matches the commit message first line
- a follow-up `git_status` shows a clean repo or at least no staged copy of the committed file

Return only:
STATUS: PASS or FAIL
TOOL: git_commit
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_push`

```text
Create or reuse a disposable git repo under `smoke/git-wave1/repo` with at least one local commit. Create a local bare remote repo under `smoke/git-wave1/remote.git` using `run_command`, then configure the working repo to use a named remote `smoke-origin` that points to that local bare repo.

Then call `git_push` with:
- `repo_dir='smoke/git-wave1/repo'`
- `remote='smoke-origin'`
- `ref='HEAD'`
- `set_upstream=false`

Verify that:
- the tool returns `pushed=true`
- a follow-up `run_command` against the bare repo confirms that the branch ref now exists there

Do not use network remotes. Return only:
STATUS: PASS or FAIL
TOOL: git_push
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_diff`

```text
Create a disposable repo under `smoke/git-wave2/repo` with one committed file `diff_target.txt`. Then modify that file using file_write without committing the change.

Call `git_diff` with:
- `repo_dir='smoke/git-wave2/repo'`
- `paths=['diff_target.txt']`
- `staged=false`

Verify that:
- `diff` is non-empty
- the diff references `diff_target.txt`
- `truncated` is false for this small case

Return only:
STATUS: PASS or FAIL
TOOL: git_diff
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_log`

```text
Create or reuse a disposable repo under `smoke/git-wave2/repo` and make at least two commits with distinct messages.

Then call `git_log` with:
- `repo_dir='smoke/git-wave2/repo'`
- `max_count=5`
- `ref='HEAD'`

Verify that:
- `commits` has at least two entries
- each entry includes `oid`, `author_name`, `author_email`, `author_time_epoch`, `author_time_iso`, and `subject`
- `parse_stats.skipped_record_count` and `parse_stats.malformed_author_time_count` are present

Return only:
STATUS: PASS or FAIL
TOOL: git_log
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_apply`

```text
Create or reuse a disposable repo under `smoke/git-wave2/repo` with a tracked file `apply_target.txt` committed in git.

Build a small unified diff that changes one line in `apply_target.txt`, then call `git_apply` with:
- `repo_dir='smoke/git-wave2/repo'`
- `patch_unified=<the diff>`
- `strip_prefix=1`
- `reject=true`
- `check=false`

Verify that:
- `applied` is true
- `touched_paths` includes `apply_target.txt`
- `rejects_created` is false
- a follow-up file_read shows the new file content

Return only:
STATUS: PASS or FAIL
TOOL: git_apply
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_branch_create`

```text
Create or reuse a disposable repo under `smoke/git-wave2/repo` with at least one commit on the current branch.

Then call `git_branch_create` with:
- `repo_dir='smoke/git-wave2/repo'`
- `name='smoke/branch-create'`
- `start_point='HEAD'`
- `checkout=true`

Verify that:
- the response includes `checked_out=true`
- if the tool times out, the failure should be `tool_runner.TIMED_OUT` with `error.details.phase`, `error.details.timed_out=true`, and timeout metadata
- a follow-up `git_status` or `run_command` confirms the current branch is `smoke/branch-create`

Return only:
STATUS: PASS or FAIL
TOOL: git_branch_create
EVIDENCE: ...
DETAILS: ...
```

### Prompt: `git_checkout`

```text
Create or reuse a disposable repo under `smoke/git-wave2/repo` with at least two branches, including `main` and `smoke/branch-create`.

Then call `git_checkout` with:
- `repo_dir='smoke/git-wave2/repo'`
- `ref='main'`
- `create=false`

Verify that:
- the response `ref` is `main`
- `detached` is false
- a follow-up `git_status` or `run_command` confirms the repo is now on `main`

Return only:
STATUS: PASS or FAIL
TOOL: git_checkout
EVIDENCE: ...
DETAILS: ...
```

## Recommended Run Order

Run the prompts in this exact order:

1. `search_code`
2. `lint_runner`
3. `typecheck_runner`
4. `webhook`
5. `git_status`
6. `git_add`
7. `git_commit`
8. `git_push`
9. `git_diff`
10. `git_log`
11. `git_apply`
12. `git_branch_create`
13. `git_checkout`

## Expected Outcome

After this sequence:

- smoke coverage should move from `11 / 24` to `24 / 24`
- git push coverage will be local-only and reproducible
- webhook coverage will either pass cleanly or surface a real contract mismatch that should be fixed explicitly

## Catalog Sync After Schema Changes

If you changed `backend/tools/registry.py` or the generated provider-facing tool schema docs, refresh the database-backed tool metadata so agents and admins see the latest descriptions and argument schemas.

Recommended refresh flow:

1. Run `python manage.py seed_tools` from `backend/` to sync the canonical registry into shared `Tool` rows.
2. In Django admin, use the `ToolDefinition` changelist action `Sync to Tools` to copy the latest shared descriptions and arg schemas down into workspace `ToolDefinition` rows.
3. If you prefer a command-driven workspace refresh, run `python manage.py seed_workspace_tools --workspace <workspace>` for the target workspace.

This is worth doing now because the git tool docs and response-field expectations were updated recently.

