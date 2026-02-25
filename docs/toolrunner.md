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
.\toolrunner\.venv\Scripts\python -m pytest toolrunner/app/tests/test_repo_tree.py -vv
```
