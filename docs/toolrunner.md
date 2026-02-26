## Toolrunner patch handling

The toolrunner’s `file_patch` tool now uses an embedded parser built on top of `pypatch==1.0.2`. That dependency is installed inside `toolrunner/.venv` and listed in `toolrunner/requirements.txt`.

### Highlights

- Requests are normalized by ensuring there is a `diff --git a/... b/...` header that matches the target path; the rest of the payload can stay in the legacy `--- a/...`/`+++ b/...` format.
- The `pypatch` parser (`pypatch.patch.fromstring`) creates `PatchSet`/`Patch`/`Hunk` objects, and we convert each hunk into the existing hunk-by-hunk application loop. Context lines, additions, deletions, backup creation, and reject file paths still behave the same as before.
- Partial applies (e.g., when `fail_on_reject=False`) emit `ok: true`, `applied_partially: true`, a `rejects_path`, `failed_hunks`, and keep the `backup_path` references that operators expect. Complete failures now return `tool_runner.PATCH_FAILED` with the same structured payload as before.

### Local verification / testing recipe

1. Ensure `toolrunner/.venv` has the dependency installed:
   - `cd toolrunner && .venv\Scripts\pip install -r requirements.txt`
2. Because Windows frequently denies access to `AppData\Local\Temp/pytest-*`, point pytest to a worktree directory that `toolrunner` owns:
   ```powershell
   $env:TMP='C:\Dev\AgentMaestro\temp'
   $env:TEMP=$env:TMP
   .\toolrunner\.venv\Scripts\python -m pytest --basetemp=C:\Dev\AgentMaestro\temp\pytest_basetemp toolrunner/app/tests/test_file_patch.py -vv
   ```
   If Pytest still cannot delete the basetemp, delete or re-ACL `C:\Dev\AgentMaestro\temp\pytest_basetemp` before rerunning.
3. For quick manual validation, run the helper script that writes a diff, calls `apply_patch`, and prints the JSON response:
   ```powershell
   cd C:\Dev\AgentMaestro
   @'
   from pathlib import Path
   from toolrunner.app.tools.file_patch import apply_patch
   from toolrunner.app.models import FilePatchArgs
   path = Path('toolrunner/tmp_manual_patch')
   import shutil, hashlib
   if path.exists():
       shutil.rmtree(path)
   path.mkdir(parents=True, exist_ok=True)
   file = path / 'target.txt'
   file.write_text('old\n')
   args = FilePatchArgs(path='target.txt', patch_unified='''--- a/target.txt
   +++ b/target.txt
   @@ -1 +1 @@
-old
+new
   ''', expected_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
   response = apply_patch(path, args)
   print(response.body)
'@ | Set-Content debug_patch_manual.py
   .\toolrunner\.venv\Scripts\python debug_patch_manual.py
   ```
   The printed JSON should include `ok: true`, the `backup_path`, and `sha256_before`/`sha256_after`.

### Notes

- Because `pypatch` looks for actual files on disk, the request path must match the file under the run directory. Encountering a patch without any hunks (e.g., `a/target.txt` but no `@@` header) will trigger `PATCH_FAILED`.
- The reject buffer still writes the original diff to `.toolrunner_rejects/<path>.rej`, so operators can inspect partial failure causes.

## Repo tree tool

The `repo_tree` tool returns a flat list of directories and files below a given root so that operators can quickly understand workspace layout without following nested recursion.

### Request structure

```json
{
  "root": ".",
  "max_depth": 6,
  "include_files": true,
  "include_dirs": true,
  "follow_symlinks": false,
  "exclude_globs": ["**/.git/**", "**/.venv/**", "**/node_modules/**", "**/__pycache__/**"],
  "include_globs": null,
  "max_entries": 5000,
  "include_metadata": true
}
```

- `root` is relative to the run directory and defaults to `"."`.
- `max_depth` bounds how many path segments below the root are emitted; directories beyond this depth are pruned.
- `include_files` / `include_dirs` toggle whether files and directories appear in the results; traversal still descends through both to discover matches.
- `follow_symlinks` controls whether symlinked directories are explored (security keeps symlinks that escape the sandbox inert).
- `exclude_globs` defaults to common noise directories such as `.git`, `.venv`, `node_modules`, and `__pycache__`.
- `include_globs` can narrow the tree to matches (patterns are matched against both the path relative to the root and the path relative to the workspace root).
- `max_entries` has a hard ceiling of 5000 entries; exceeding the limit results in `truncated: true`.
- `include_metadata` controls whether `size_bytes` and `mtime_epoch` return for each entry.

### Response highlights

- Entries are sorted lexicographically by `path`.
- Each entry reports `type`, `path`, `depth`, and optional metadata when requested.
- The `stats` block summarizes how many files/dirs were returned, and `truncated` flags when the result set hit `max_entries`.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_repo_tree.py -vv
```

## Search code tool

Searching for text or regex across the run workspace helps operators locate relevant files without opening every file manually. The tool honors include/exclude globs, limits, and timeouts so scans stay bounded.

### Request structure

```json
{
  "query": "get_logger\\(",
  "is_regex": true,
  "case_sensitive": false,
  "root": ".",
  "include_globs": ["**/*.py", "**/*.js", "**/*.html"],
  "exclude_globs": ["**/.git/**", "**/.venv/**", "**/node_modules/**"],
  "max_results": 100,
  "max_matches_per_file": 20,
  "context_lines": 2,
  "timeout_ms": 3000
}
```

- `query` is required; `is_regex` toggles whether it is interpreted as a regular expression. Defaults favor case-insensitive literal search.
- `root` is relative to the run directory; globs are matched both against paths relative to that root and the workspace.
- `include_globs` restrict the files that are read, while `exclude_globs` (defaulting to `.git`, `.venv`, `node_modules`) keep noisy directories out of the scan.
- `max_results` caps the total matches reported; `max_matches_per_file` limits how many snippets appear per file. Hitting either limit marks `truncated` true.
- `context_lines` controls how many lines before/after each match are returned, and `timeout_ms` bounds how long the search may run.

### Response highlights

- `matches` is a path-sorted list of files that contained results; each entry includes `match_count` and the snippet list (line number, column, line text, and surrounding context).
- `stats` reports how many files were scanned, how many had matches, and the total matched occurrences counted.
- `truncated` becomes `true` when timeouts or any of the max limits fire.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_search_code.py -vv
```

## Run command tool

The `run_command` tool is the Tier 2 primitive for executing deterministic processes (PowerShell, Python, node, etc.) inside a workspace-safe sandbox. It always captures `stdout`/`stderr`, enforces a timeout, honors working-directory constraints, and respects per-run output limits.

### Request structure

```json
{
  "cmd": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/test.ps1"],
  "cwd": ".",
  "env": { "PYTHONNOUSERSITE": "1" },
  "timeout_ms": 300000,
  "max_output_bytes": 262144,
  "stdin_text": null
}
```

- `cmd` is required and is always provided as an array of arguments; shell parsing is disallowed so the tool stays narrow and deterministic.
- `cwd` is workspace-relative and is filtered through `safe_join` so escaping the run directory is rejected with `PATH_OUTSIDE_WORKSPACE`.
- `env`, if supplied, is merged on top of `os.environ`.
- `timeout_ms` and `max_output_bytes` bound how long the process may run and how much output is retained. Timeouts now kill the entire process tree (`taskkill /T /F` on Windows, `kill()` on POSIX) so long-running tests don’t leak subprocesses.
- `stdin_text` feeds the process over stdin when provided.
- Output is always decoded as UTF-8, truncated by bytes, and stable regardless of the host locale.
- Errors include a `details` object (`{"cwd": <requested>}` when the working directory is missing, `{"cmd0": ...}` when the requested binary cannot be found) in addition to the `tool_runner.<code>` envelope.

### Response highlights

- The success envelope is `{ "ok": true, "result": { ... } }` with `exit_code`, `duration_ms`, `timed_out`, `stdout`, `stderr`, and the `_truncated` flags.
- On timeout, `timed_out` flips to `true`, `exit_code` is `null`, and partial output is still returned so the caller can reason about what was captured.
- Errors (bad cwd, missing binary, etc.) return `ok: false` plus the `tool_runner.<code>` envelope with `INVALID_ARGUMENT`, `NOT_FOUND`, `PERMISSION_DENIED`, or other standard Tier 2 codes.
- Output text is truncated to `max_output_bytes`, and `stdout_truncated`/`stderr_truncated` indicate the truncation state.
- Execution always merges the caller’s env with the host OS env, so built-in executables (e.g., `python`) still resolve when the tool runs inside a sandbox.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_run_command.py -vv
```

## Test runner tool

`test_runner` wraps the approved test entrypoints (PowerShell scripts, Pytest, or an explicit command) and returns a structured summary with counts, failure details, and captured output.

### Request structure

```json
{
  "kind": "powershell_script",
  "script_path": "scripts/test.ps1",
  "script_args": ["-q"],
  "cwd": ".",
  "env": {},
  "timeout_ms": 600000,
  "max_output_bytes": 524288,
  "parse": "pytest"
}
```

- `kind` selects `powershell_script`, `pytest`, or `command`.
- `script_path`/`script_args` only apply to `powershell_script` and must live inside the workspace. Script arguments are appended after `--` so they reach the script rather than PowerShell itself.
- `pytest_args` are required when running the `pytest` kind, while `cmd` is required for the `command` kind.
- `parse` defaults to `pytest` and enables summary/failure extraction; set it to `none` to skip parsing and preserve raw output.
- When `kind=pytest` the tool always runs `["python", "-m", "pytest", ...]` so the request uses the same interpreter as the agent and avoids PATH surprises.

### Response highlights

- A successful response includes `exit_code`, `duration_ms`, `timed_out`, parsed `summary`, `failed_tests`, and captured `stdout`/`stderr` with truncation flags.
- The summary tracks `passed`, `failed`, `skipped`, `xfailed`, `xpassed`, and `errors`, while each failed test entry exposes the node id, file, message, and traceback snippet.
- Responses now also include `parse_mode` so callers know how the stdout/stderr was interpreted (e.g., `"pytest"` vs `"none"`).
- Errors (bad cwd, missing script, unsupported fields) propagate standard `tool_runner.<CODE>` envelopes just like other Tier 2 helpers.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_test_runner.py -vv
```

## Lint runner tool

`lint_runner` invokes approved linters (currently Ruff, Flake8, ESLint, Prettier, or an explicit command) and returns structured issues plus stdout/stderr.

### Request structure

```json
{
  "tool": "ruff",
  "cwd": ".",
  "paths": ["app", "toolrunner"],
  "args": ["check", "."],
  "timeout_ms": 180000,
  "max_output_bytes": 262144,
  "parse": "ruff"
}
```

- `paths` are workspace-relative directories that are appended to the linter command and are individually sandbox-checked.
- Each supported tool has a default argument list (e.g., Ruff uses `["check"]` if you omit `args`), but you can override them via `args`.
- `tool="command"` runs the provided `cmd` array and defaults to `parse="none"`.
- When `parse="ruff"`, the tool adds `--output-format=json` and translates Ruff’s JSON into `issues`.

### Response highlights

- `issues` is always returned (empty list when parsing is disabled) and includes `path`, `line`, `col`, `code`, `severity`, and `message`.
- `parse_mode` mirrors the request so callers know how the output was interpreted.
- Standard Tier 2 metadata (`exit_code`, `duration_ms`, `timed_out`, `stdout`, `stderr`, truncation flags) is included just as in other agents.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_lint_runner.py -vv
```

## Typecheck runner tool

`typecheck_runner` supports Pyright, Mypy, TSC, or an explicit command and emits structured diagnostics along with stdout/stderr.

### Request structure

```json
{
  "tool": "pyright",
  "cwd": ".",
  "args": [],
  "timeout_ms": 300000,
  "max_output_bytes": 262144,
  "parse": "pyright"
}
```

- `args` override the default flag set (Pyright adds `--outputjson`, Mypy uses `--show-column-numbers --show-error-context`, TSC uses `--pretty false` unless you override).
- `tool="command"` runs the provided `cmd` array and defaults to `parse="none"`.
- Supported parse modes (`pyright`, `mypy`, `tsc`) convert the checkers’ output into structured diagnostics.

### Response highlights

- `diagnostics` is always present and contains entries with `path`, `line`, `col`, `severity`, `code`, and `message`.
- Add `parse_mode` to see how the result was interpreted.
- `parse_source` indicates whether the diagnostics were read from stdout/stderr (or `none` when parsing is disabled), and `parse_warning` surfaces any issues (like truncated/invalid output).
- Standard Tier 2 metadata (`exit_code`, `duration_ms`, `timed_out`, `stdout`, `stderr`, truncation flags) mirrors other tools.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_typecheck_runner.py -vv
```

## Format runner tool

`format_runner` invokes Ruff Format (Black/Prettier/command placeholder) in `check` or `apply` mode and returns any changed paths plus stdout/stderr metadata.

### Request structure

```json
{
  "tool": "ruff_format",
  "mode": "check",
  "cwd": ".",
  "paths": ["app", "toolrunner"],
  "timeout_ms": 180000,
  "max_output_bytes": 262144
}
```

- `mode` controls whether formatting is verified (`check`, adds `--check --diff`) or applied (`apply`, adds `--diff`).
- `paths` are workspace-relative; they are sandbox-checked and appended to the formatter invocation.
- `tool="command"` runs the user-supplied `cmd` array and skips the default Ruff invocation.

### Response highlights

- `changed_files` lists any files reported by the formatter (currently parsed from Ruff’s `+++ path` diff lines).
- Includes `exit_code`, `duration_ms`, `timed_out`, `stdout`, `stderr`, and truncation flags like other Tier 2 tools.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_format_runner.py -vv
```

## Git diff tool

`git_diff` returns a unified diff from the workspace, supporting staged/unstaged and path filtering.

### Request structure

```json
{
  "repo_dir": ".",
  "staged": false,
  "paths": ["toolrunner/app/file_patch.py"],
  "context_lines": 3,
  "detect_renames": true,
  "timeout_ms": 60000,
  "max_output_bytes": 524288
}
```

- `repo_dir` is workspace-relative and must be inside the sandbox.
- `staged` toggles `git diff --cached`.
- `paths` limits the diff to the provided files; omit for full diff.
- `context_lines` controls `-U`; `detect_renames` adds `--find-renames`.

### Response highlights

- Returns the normalized diff string plus the raw stdout/stderr and a `truncated` flag.
- Standard Tier 2 metadata (`exit_code`, `duration_ms`, `timed_out`) is also returned.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_diff.py -vv
```

## Git branch create tool

`git_branch_create` creates a new branch and optionally checks it out.

### Request structure

```json
{
  "repo_dir": ".",
  "name": "agent/2026-02-25-filepatch",
  "start_point": "HEAD",
  "checkout": true,
  "force": false
}
```

- `force` adds `-f` so you can recreate branches.
- `checkout` switches to the new branch using `git switch`.

### Response highlights

- Returns the repo dir, the branch name, and whether it was checked out.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_branch_create.py -vv
```

## Git add tool

`git_add` stages files with optional `-A`/`-N`.

### Request structure

```json
{
  "repo_dir": ".",
  "paths": ["toolrunner/app/file_patch.py", "toolrunner/app/file_read.py"],
  "all": false,
  "intent_to_add": false
}
```

- `all` runs `git add -A`.
- `intent_to_add` adds `-N`.

### Response highlights

- Returns the repo dir and normalized staged paths.
- Includes raw `stdout`/`stderr` plus truncation flags for debugging.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_add.py -vv
```

## Git status tool

`git_status` inspects the current working tree via `git status --porcelain=v2 --branch`, returning structured branch metadata, staged/unstaged/untracked paths, conflicts, and a clean flag.

### Request structure

```json
{
  "repo_dir": ".",
  "porcelain": "v2",
  "include_untracked": true,
  "timeout_ms": 30000,
  "max_output_bytes": 262144
}
```

- `repo_dir` is relative to the workspace and must stay inside the sandbox.
- `porcelain` accepts `v1` or `v2` (defaults to `v2`); we recommend `v2` for branch tracking.
- `include_untracked` controls whether the command emits `--untracked-files=no` (set it to `false` to ignore untracked files).
- `timeout_ms` and `max_output_bytes` bound how long the git status invocation may run and how much output is retained.

### Response highlights

- `branch` reports the current branch name, OID, upstream, ahead/behind counts, and a `detached` flag.
- `staged`, `unstaged`, `untracked`, and `conflicts` are populated from the porcelain output; `is_clean` flips to `true` only when every list is empty (and untracked files are ignored when `include_untracked=false`).
- `raw.stdout`/`raw.stderr` expose normalized output (`\n` only) plus `stdout_truncated`/`stderr_truncated`, keeping the same truncation metadata that `run_command` provides.
- Non-repo errors return `tool_runner.NOT_FOUND` (or `tool_runner.INVALID_ARGUMENT` for other git failures) with the stderr details.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_status.py -vv
```

## Git checkout tool

`git_checkout` wraps `git checkout` so operators can switch branches, tags, or commits from inside the run workspace.

### Request structure

```json
{
  "repo_dir": ".",
  "ref": "main",
  "create": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

- `max_output_bytes` can be provided to bound the amount of stdout/stderr stored (defaults to 262144).

- `repo_dir` stays inside the sandbox (default `.`).
- `ref` is required and can be a branch, tag, or commit-ish.
- `create` runs `git checkout -b <ref>` from the current HEAD.
- `timeout_ms` bounds how long the checkout may run.

### Response highlights

- `repo_dir`, `ref`, and a `detached` boolean show the final state.
- `raw.stdout`/`raw.stderr` contain normalized newline output plus truncation flags from `run_command`.
- On checkout failures (bad ref, dirty worktree, missing repo) the error bubbles up as `tool_runner.INVALID_ARGUMENT` or `tool_runner.NOT_FOUND`.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_checkout.py -vv
```

## Git commit tool

`git_commit` stages files and creates commits inside the run workspace, exposing `--signoff`, `--amend`, and collection of changed-file metadata.

### Request structure

```json
{
  "repo_dir": ".",
  "message": "Fix file_patch: strip_prefix + line ending normalization",
  "paths_to_add": ["toolrunner/app/file_patch.py"],
  "add_all": false,
  "signoff": false,
  "amend": false,
  "timeout_ms": 60000,
  "max_output_bytes": 262144
}
```

- `repo_dir` stays inside the sandbox.
- `message` is required and becomes the commit message (the summary is the first line).
- `paths_to_add` stages the listed paths before committing; it accepts `null` if nothing specific should be staged.
- `add_all` runs `git add --all` to stage the entire working tree.
- `signoff` and `amend` translate to `--signoff` and `--amend` on `git commit`.
- `timeout_ms` and `max_output_bytes` bound git’s runtime and output capture.

### Response highlights

- `commit_oid` is `git rev-parse HEAD`, `summary` is your message’s first line, and `changed_files` counts `git diff-tree --name-only -r HEAD`.
- `raw.stdout`/`raw.stderr` keep normalized output plus truncation flags so callers can diagnose commit failures.
- When nothing to commit exists, the tool returns `tool_runner.CONFLICT` with message “nothing to commit.”

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_commit.py -vv
```

## Git log tool

`git_log` exposes recent commits (oid, author, timestamp, subject) via `git log --format=...`.

### Request structure

```json
{
  "repo_dir": ".",
  "max_count": 20,
  "ref": "HEAD"
}
```

- `max_count` bounds how many commits to read (defaults to 20).
- `ref` selects the starting commit (defaults to `HEAD`).
- The tool respects the sandboxed `repo_dir`.

### Response highlights

- `commits` is a list of `{oid, author_name, author_email, author_time_epoch, subject}` entries.
- Includes `repo_dir`, `ref`, and `max_count` for easier caller bookkeeping.
- `raw.stdout` holds the normalized git output so callers can reparse if needed.
- Errors (bad repo, bad ref) surface via the usual `tool_runner.*` envelope.
- If git truncates the stdout, the tool now reports `parse_warning`.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_git_log.py -vv
```

## Coverage runner tool

`coverage_runner` executes `coverage run -m pytest` followed by `coverage json` to emit overall coverage plus per-file percents.

### Request structure

```json
{
  "kind": "pytest_coverage",
  "cwd": ".",
  "timeout_ms": 600000,
  "max_output_bytes": 524288
}
```

- `kind=pytest_coverage` is currently the only supported scenario; it runs pytest under coverage.
- You can supply extra pytest flags via `args` if needed.
- The tool writes a temporary `coverage.json` in the working directory to read the report.

### Response highlights

- Returns `total_percent` plus a list of `{path, percent}` entries from the coverage JSON.
- Standard metadata (`exit_code`, `duration_ms`, `timed_out`, `stdout`, `stderr`, truncation flags) is still present.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_coverage_runner.py -vv
```

## Orchestrator UI Dashboard

The FastAPI `toolrunner.app.main` module now serves a `/ui` dashboard that mirrors the Maestro/Apprentice workflow:

- **User tab:** A chat-first experience where Maestro poses clarifying questions, drafts sections, and locks them conversationally. Each turn is appended to `.agentmaestro/runs/<run_id>/chat/transcript.jsonl`, updates the preview/completeness widgets, and still flows through `SRS.md`/`SRS.lock.json` while emitting `CHAT_MESSAGE`, `SRS_UPDATED`, and `SRS_SECTION_LOCKED` events.
- **Maestro tab:** Displays the latest synthesized plan summary, the new SRS readiness score/missing items, and raw JSON while letting you regenerate a schema-valid `plan.json` via the new `/v1/runs/{run_id}/plan/generate` endpoint (plan generation is gated until readiness ≥ 60 unless you override).
- **Apprentice tab:** Shows start/stop controls plus an event feed that polls `/v1/runs/{run_id}/events`, reflecting SRS events, approvals, plan generation, and orchestrator activity.

Run artifacts are persisted per run:

- `charter.json`, `plans/<plan_id>.json`, `plans/latest.json`, `step_reports/<milestone_id>/<step_id>.json`
- `events.jsonl` (append-only event log; use `events_meta.json` for the last event ID)
- `chat/transcript.jsonl` (one JSON document per Maestro/User message maintaining the conversation history)
- `srs/SRS.md` and `srs/SRS.lock.json`
- `srs/readiness.json` (cached gate report: score, checks, counts, missing items, and warnings)
- `approvals.json` via the `/v1/runs/{run_id}/approve` endpoint

Key API routes for the dashboard workflows:

- `POST /v1/runs` – create a run (`slug`, optional `repo_dir`/`srs_path`)
- `GET /v1/runs/{run_id}` – status snapshot
- `POST /v1/runs/{run_id}/start` / `stop` – orchestrator control
- `GET /v1/runs/{run_id}/events?since=<id>` – poll for new events
- `GET /v1/runs/{run_id}/srs/...` – list sections, prompts, markdown, lock metadata
- `GET /v1/runs/{run_id}/srs/readiness` – compute/read the readiness report (score, missing items, warnings, and counts)
- `POST /v1/runs/{run_id}/srs/...` – save drafts or lock a section
- `POST /v1/runs/{run_id}/plan/generate` and `GET .../plan` – synthesize and read plans
- `POST /v1/runs/{run_id}/approve` – record Maestro approvals
- `GET /v1/runs/{run_id}/step_reports` and `/step_reports/{milestone_id}/{step_id}` – surface step report metadata + payload for the Apprentice tab
- `POST /v1/runs/{run_id}/chat` – chat with Maestro, receive structured replies, and surface any applied SRS updates/preview metadata
- `GET /v1/runs/{run_id}/chat/history` – paginate the stored transcript (supports `?since=<event_id>`)
- `POST /v1/runs/{run_id}/chat/reset` – truncate the chat log for a run and emit a `CHAT_RESET` event

### Running the UI

1. Ensure the FastAPI app is running (e.g., `uvicorn toolrunner.app.main:app --reload` from `toolrunner/`).
2. Visit `http://localhost:8000/ui`. The default run created at startup powers the tabs and event feed.
3. Use the User tab to draft/lock sections, the Maestro tab to generate plans, and the Apprentice tab for events.

### Verification

```powershell
cd C:\Dev\AgentMaestro
.\toolrunner\scripts\test.ps1 app/tests/test_ui_pages.py app/tests/test_srs_endpoints.py app/tests/test_events_feed.py app/tests/test_plan_generate.py app/tests/test_step_reports.py -vv
```
- `POST /v1/runs/{run_id}/plan/generate` – synthesize a schema-valid plan once at least one SRS section is locked (steps that touch the “Risks & Assumptions” section automatically flag approval requests and emit `PLAN_GENERATED` events).
