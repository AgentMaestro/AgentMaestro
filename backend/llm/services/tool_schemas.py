from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Dict, List

from tools.registry import TOOL_REGISTRY as CANONICAL_TOOL_REGISTRY


def _base_tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def _schema_docs(
    required_parameters: List[str],
    examples: Dict[str, Any] | List[Dict[str, Any]] | None,
    response_fields: Dict[str, str] | None = None,
) -> str:
    lines: list[str] = ["REQUIRED PARAMETERS:"]
    if required_parameters:
        for name in required_parameters:
            lines.append(f"- {name}")
    else:
        lines.append("- none")
    example_items: list[Dict[str, Any]] = []
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


_FALLBACK_TOOL_NAMES = [
    "repo_tree",
    "search_code",
    "file_read",
    "file_write",
    "file_delete",
    "file_patch",
    "shell_exec",
    "python_exec",
    "git_add",
    "git_status",
    "git_diff",
    "git_log",
    "git_apply",
    "git_branch_create",
    "git_checkout",
    "git_push",
    "run_command",
    "test_runner",
    "format_runner",
    "coverage_runner",
    "lint_runner",
    "typecheck_runner",
]


_TOOL_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "repo_tree": {
        "path": "C:\\Dev\\AgentMaestro\\backend\\runs\\tests\\fixtures\\tool_repo",
        "max_depth": 3,
        "include_files": True,
        "include_dirs": True,
        "follow_symlinks": False,
    },
    "search_code": [
        {
            "query": "provider_call_id",
            "is_regex": False,
            "case_sensitive": False,
            "root": "backend",
            "include_globs": ["**/*.py"],
            "max_results": 5,
            "timeout_ms": 3000,
        },
        {
            "query": "provider_call_id",
            "is_regex": False,
            "case_sensitive": False,
            "root": "C:\\Dev\\AgentMaestro\\backend",
            "include_globs": ["**/*.py"],
            "max_results": 5,
            "timeout_ms": 3000,
        },
    ],
    "file_read": {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py", "mode": "text"},
    "file_write": {
        "path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py",
        "content": "print('hello')\n",
        "overwrite": True,
    },
    "file_delete": {"path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py"},
    "file_patch": {
        "path": "C:\\tmp\\agentmaestro\\smoke_tools\\hello.py",
        "patch_unified": "--- a/hello.py\n+++ b/hello.py\n@@ -1,1 +1,1 @@\n-print('hello')\n+print('hello world')\n",
    },
    "shell_exec": {
        "cmd": ["powershell", "-NoProfile", "-Command", "Get-Location"],
        "cwd": ".",
    },
    "python_exec": {"code": "from pathlib import Path\nprint(Path('C:/Dev/AgentMaestro').exists())"},
    "git_add": {"paths": ["backend/tools/admin.py"]},
    "git_status": {
        "repo_dir": "C:\\Dev\\AgentMaestro",
        "porcelain": "v1",
        "include_untracked": True,
    },
    "git_diff": {
        "repo_dir": "C:\\Dev\\AgentMaestro",
        "paths": ["backend/tools/admin.py"],
        "staged": False,
    },
    "git_log": {"repo_dir": "C:\\Dev\\AgentMaestro", "max_count": 5},
    "git_apply": {
        "repo_dir": "C:\\Dev\\AgentMaestro",
        "patch_unified": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
    },
    "git_branch_create": {
        "repo_dir": "C:\\Dev\\AgentMaestro",
        "name": "smoke/tool-docs",
        "checkout": False,
    },
    "git_checkout": {"repo_dir": "C:\\Dev\\AgentMaestro", "ref": "main"},
    "git_push": {
        "repo_dir": "C:\\Dev\\AgentMaestro",
        "remote": "origin",
        "ref": "main",
    },
    "run_command": {
        "cmd": ["cmd", "/C", "echo RUN_COMMAND_SMOKE_OK && dir C:\\Dev\\AgentMaestro\\toolrunner\\app\\tools"],
        "cwd": ".",
    },
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
    "lint_runner": [
        {"tool": "ruff", "cwd": ".", "paths": ["backend/tools"]},
        {"tool": "ruff", "cwd": "C:\\Dev\\AgentMaestro", "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools"]},
    ],
    "typecheck_runner": [
        {"tool": "mypy", "cwd": ".", "args": ["backend"], "timeout_ms": 300000, "max_output_bytes": 262144},
        {"tool": "mypy", "cwd": "C:\\Dev\\AgentMaestro", "args": ["C:\\Dev\\AgentMaestro\\backend"], "timeout_ms": 300000, "max_output_bytes": 262144},
    ],
}


_TOOL_RESPONSE_FIELDS: Dict[str, Dict[str, str]] = {
    "file_read": {
        "path": "Echoes the requested path value.",
        "mode": "Whether the returned payload is text or binary.",
        "content": "Returned text when mode=text.",
        "content_base64": "Returned bytes when mode=binary.",
        "truncated": "True when max_bytes limited the response.",
    },
    "file_write": {
        "resolved_path": "The exact filesystem path that was written.",
        "created": "True when the file did not exist before the write.",
        "overwritten": "True when an existing file was replaced.",
        "bytes_written": "Number of bytes written to disk.",
    },
    "file_patch": {
        "applied": "True when all hunks applied.",
        "applied_partially": "True when some hunks applied and rejects were produced.",
        "backup_path": "Backup copy location when backup=true.",
        "rejects_path": "Reject file location when patching fails.",
    },
    "file_delete": {
        "resolved_path": "The exact filesystem path that was targeted for deletion.",
        "deleted": "True when a file or directory was removed.",
        "missing": "True when missing_ok=true and the target did not exist.",
        "deleted_type": "Whether the deleted target was a file or directory.",
    },
    "repo_tree": {
        "root": "The root path that was listed.",
        "entries": "Sorted files/directories found under that root.",
        "stats": "Counts, exclusions, and allowed roots used by policy.",
    },
    "search_code": {
        "matches": "Path-sorted files with snippets for each match; snippets include line, col, and line_text when available.",
        "stats": "File counts, total matches, and exclusion totals.",
        "truncated": "True when max_results or timeout limits stopped the scan.",
    },
    "shell_exec": {
        "command": "The executed command array.",
        "cwd": "The working directory used for execution.",
        "exit_code": "Process exit status, or null if timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text.",
    },
    "python_exec": {
        "command": "The inferred execution command or entrypoint.",
        "cwd": "The working directory used for execution.",
        "exit_code": "Process exit status, or null if timed out.",
        "stdout": "Captured Python stdout.",
        "stderr": "Captured Python stderr.",
    },
    "git_status": {
        "branch": "Current branch name when available.",
        "entries": "Structured git status rows.",
        "stdout": "Raw git status output.",
    },
    "git_diff": {
        "diff": "Unified diff text.",
        "stdout": "Raw git diff output.",
        "exit_code": "Git process exit code.",
    },
    "git_log": {
        "commits": "Structured recent commit metadata.",
        "stdout": "Raw git log output.",
    },
    "git_add": {
        "stdout": "Raw git add output.",
        "stderr": "Raw git add errors, if any.",
        "exit_code": "Git process exit code.",
    },
    "git_apply": {
        "stdout": "Raw git apply output.",
        "stderr": "Raw git apply errors, if any.",
        "exit_code": "Git process exit code.",
    },
    "git_branch_create": {
        "stdout": "Raw git branch output.",
        "stderr": "Raw git branch errors, if any.",
        "exit_code": "Git process exit code.",
    },
    "git_checkout": {
        "stdout": "Raw git checkout output.",
        "stderr": "Raw git checkout errors, if any.",
        "exit_code": "Git process exit code.",
    },
    "git_push": {
        "stdout": "Raw git push output.",
        "stderr": "Raw git push errors, if any.",
        "exit_code": "Git process exit code.",
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
    "format_runner": {
        "changed_files": "Files detected as changed by formatter output parsing.",
        "parse_mode": "The formatter/parser mode used to interpret output.",
        "stdout": "Captured formatter stdout.",
        "stderr": "Captured formatter stderr.",
        "python_interpreter": "Interpreter path used for Python-backed formatter modes such as ruff_format.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "lint_runner": {
        "issues": "Parsed lint findings when the selected parser supports it.",
        "parse_mode": "The parser mode used to interpret linter output.",
        "stdout": "Captured linter stdout.",
        "stderr": "Captured linter stderr.",
        "python_interpreter": "Interpreter path used for Python-backed linter modes such as ruff.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
    "run_command": {
        "exit_code": "The process exit code, or null if the process timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text.",
        "timed_out": "True when the process exceeded its timeout.",
        "timeout_source": "Which timeout setting was enforced for the command.",
    },
    "typecheck_runner": {
        "diagnostics": "Parsed type-check findings when the selected parser supports it.",
        "parse_mode": "The parser mode used to interpret output.",
        "stdout": "Captured type-check stdout.",
        "stderr": "Captured type-check stderr.",
        "python_interpreter": "Interpreter path used for Python-backed type checkers such as mypy and pyright.",
        "python_interpreter_source": "Whether the interpreter came from TOOLRUNNER_PYTHON or fallback discovery.",
    },
}


_TOOL_ADDITIONAL_DOCS: Dict[str, str] = {
    "file_patch": "\n\nPATCH FORMAT NOTES:\n"
    "- `patch_unified` is required.\n"
    "- Provide a complete unified diff for a single target file.\n"
    "- Include `---` and `+++` file markers and at least one `@@` hunk header.\n"
    "- Hunk headers must use explicit, accurate unified diff ranges, for example `@@ -1,2 +1,2 @@` or `@@ -0,0 +1,3 @@`.\n"
    "- Shorthand headers like `@@ -1 +1 @@` are rejected. Even pure insertions must include counts.\n"
    "- Do not include `*** Begin Patch` / `*** End Patch` fences; this tool expects only unified diff text.\n",
    "search_code": "\n\nPATH NOTES:\n"
    "- `root` may be omitted, repo-relative, or absolute.\n"
    "- Repo-relative `root` values resolve from the repository root when one is provided in policy context.\n"
    "- Absolute `root` values are permitted only when they fall under the allowed roots for the run.\n"
    "- `include_globs` are evaluated relative to the provided `root`.\n\n"
    "RESULT NOTES:\n"
    "- Match snippets include `line`, `col`, and `line_text` when the tool can derive them from the scanned text.",
    "shell_exec": "\n\nCOMMAND ARGUMENT NOTES:\n"
    "- `cmd` is required and must be an array of strings.\n"
    "- Pass the executable as the first element and each argument as a separate item.\n"
    "- Use `cwd` to control the working directory.\n",
    "python_exec": "\n\nPYTHON EXECUTION NOTES:\n"
    "- Provide either `code` or `entrypoint`.\n"
    "- If `entrypoint` is supplied, include `files` so the runner has the staged file contents it should execute.\n",
    "test_runner": "\n\nRUN MODE NOTES:\n"
    "- `kind` is required.\n"
    "- Supported kinds are `powershell_script`, `pytest`, and `command`.\n"
    "- Choose exactly one mode:\n"
    "  - `kind=powershell_script` requires `script_path`\n"
    "  - `kind=pytest` requires `pytest_args`\n"
    "  - `kind=command` requires `cmd`\n"
    "- Prefer `kind=pytest` for narrow smoke tests and `kind=powershell_script` for repo-standard test entrypoints.\n"
    "- `cwd` may be absolute or repo-relative.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `pytest` mode runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `pytest` is missing, the tool reports the resolved interpreter path and source.\n",
    "coverage_runner": "\n\nRUN MODE NOTES:\n"
    "- `kind` is required and currently must be `pytest_coverage`.\n"
    "- Provide pytest target arguments via `args`.\n"
    "- Coverage generates a `coverage.json` artifact in the working directory.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- Coverage commands run through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `coverage` or `pytest` is missing, the tool reports the resolved interpreter path and source.\n",
    "format_runner": "\n\nRUN MODE NOTES:\n"
    "- `tool` is required.\n"
    "- Supported formatter modes are `ruff_format`, `black`, `prettier`, and `command`.\n"
    "- Choose `mode=check` for validation-only or `mode=apply` to write changes.\n"
    "- `cwd` and each item in `paths` may be absolute or repo-relative.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff_format` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.\n",
    "lint_runner": "\n\nRUN MODE NOTES:\n"
    "- `tool` is required.\n"
    "- Supported values are `ruff`, `flake8`, `eslint`, `prettier`, and `command`.\n"
    "- `cwd` and each item in `paths` may be absolute or repo-relative.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.\n",
    "run_command": "\n\nCOMMAND ARGUMENT NOTES:\n"
    "- `cmd` is required and must be a list of strings.\n"
    "- Pass the executable and each argument as separate list items.\n"
    "- If you need shell features such as `&&`, invoke a shell explicitly.\n",
    "typecheck_runner": "\n\nRUN MODE NOTES:\n"
    "- `tool` is required.\n"
    "- Supported values are `mypy`, `pyright`, `tsc`, and `command`.\n"
    "- `cwd` may be absolute or repo-relative.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "DEFAULT LIMITS:\n"
    "- `timeout_ms` defaults to `300000`.\n"
    "- `max_output_bytes` defaults to `262144`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `mypy` and `pyright` run through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If a Python-backed type checker is missing, the tool reports the resolved interpreter path and source.\n",
}


def _released_registry_tools() -> Dict[str, Dict[str, Any]]:
    tool_map: Dict[str, Dict[str, Any]] = {}
    for group in CANONICAL_TOOL_REGISTRY:
        for tool in group.get("tools", []):
            if not tool.get("released", True):
                continue
            tool_map[str(tool["name"])] = deepcopy(tool)
    return tool_map


def _default_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _canonical_parameters(tool_name: str, tool_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    tool = tool_map[tool_name]
    schema = deepcopy(tool.get("args_schema") or _default_schema())
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


_RELEASED_TOOL_MAP = _released_registry_tools()

_TOOL_SCHEMAS = [
    _base_tool(
        tool_name,
        str(_RELEASED_TOOL_MAP[tool_name].get("description") or ""),
        _canonical_parameters(tool_name, _RELEASED_TOOL_MAP),
    )
    for tool_name in _FALLBACK_TOOL_NAMES
]


for tool in _TOOL_SCHEMAS:
    parameters = tool.get("parameters") or {}
    required_parameters = list(parameters.get("required") or [])
    examples = _TOOL_TEMPLATES.get(tool["name"])
    docs = _schema_docs(required_parameters, examples, _TOOL_RESPONSE_FIELDS.get(tool["name"]))
    docs = f"{docs}{_TOOL_ADDITIONAL_DOCS.get(tool['name'], '')}"
    existing = str(parameters.get("description") or "").strip()
    parameters["description"] = f"{existing}\n\n{docs}".strip() if existing else docs
    if examples is not None:
        if isinstance(examples, list):
            parameters["examples"] = deepcopy(examples)
        else:
            parameters["examples"] = [deepcopy(examples)]


def get_tool_schemas() -> List[Dict[str, Any]]:
    return deepcopy(_TOOL_SCHEMAS)


def get_tool_arg_templates() -> Dict[str, Dict[str, Any]]:
    return deepcopy(_TOOL_TEMPLATES)
