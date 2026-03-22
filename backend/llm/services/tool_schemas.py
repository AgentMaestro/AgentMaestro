from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List

from google_bridge.services.schema import (
    GOOGLE_BRIDGE_TOOL_EXAMPLES,
    GOOGLE_BRIDGE_TOOL_NAME,
    GOOGLE_BRIDGE_TOOL_RESPONSE_FIELDS,
)
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
    "search_files",
    "list_symbols",
    "find_symbol",
    "find_references",
    "jump_to_symbol",
    "search_code",
    "web_search",
    "fetch_url",
    "remember",
    "search_memory",
    "schedule_task",
    "edit_scheduled_task",
    "disable_scheduled_task",
    "enable_scheduled_task",
    GOOGLE_BRIDGE_TOOL_NAME,
    "list_scheduled_tasks",
    "get_current_datetime",
    "spawn_subrun",
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
    "run_command_safe",
    "test_runner",
    "run_tests",
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
    "search_files": [
        {
            "query": "code_navigation.py",
            "scope": "toolrunner/app/tools",
            "include_globs": ["**/*.py"],
        },
        {
            "query": "code_navigation*",
            "scope": "toolrunner/app/tools",
            "include_globs": ["**/*.py"],
            "compact": True,
        },
        {
            "query": "code_navigation.py|run_command_safe",
            "scope": "toolrunner/app/tools",
            "include_globs": ["**/*.py"],
            "is_regex": True,
            "compact": True,
        },
    ],
    "list_symbols": [
        {"scope": "toolrunner/app/tools/code_navigation.py", "include_docstrings": True},
        {
            "scope": "toolrunner/app/tools/code_navigation.py",
            "include_private": True,
            "include_docstrings": True,
            "compact": True,
        },
    ],
    "find_symbol": [
        {"symbol_name": "ToolCatalogTests", "scope": "backend/tools/tests", "kind": "class"},
        {
            "symbol_name": "list_search_code",
            "scope": "toolrunner/app/tools",
            "kind": "function",
            "fuzzy": False,
            "language": "python",
            "compact": True,
        },
        {
            "symbol_name": "find_symbol",
            "scope": "toolrunner/app/tools/code_navigation.py",
            "kind": "function",
            "fuzzy": True,
            "language": "python",
            "compact": True,
        },
    ],
    "find_references": [
        {
            "symbol": "search_code",
            "scope": "backend",
            "include_declarations": True,
            "include_comments": False,
            "context_lines": 4,
        },
        {
            "symbol": "search_code",
            "scope": "backend",
            "include_declarations": False,
            "include_comments": False,
            "context_lines": 4,
            "compact": True,
        },
        {
            "symbol": "list_search_code",
            "scope": "toolrunner",
            "include_strings": False,
            "context_lines": 4,
            "compact": True,
        },
    ],
    "jump_to_symbol": [
        {"symbol": "search_code", "scope": "backend", "context_lines": 8},
        {
            "symbol": "find_symbol",
            "scope": "toolrunner/app/tools/code_navigation.py",
            "kind": "function",
            "fuzzy": True,
            "context_lines": 8,
            "compact": True,
        },
        {"symbol": "list_search_code", "scope": "toolrunner", "context_lines": 8, "compact": True},
    ],
    "run_command_safe": {
        "argv": ["python", "manage.py", "check"],
        "cwd": ".",
        "timeout_seconds": 60,
        "timeout_ms": 60000,
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
    "python_exec": {
        "code": "from pathlib import Path\nprint(Path('C:/Dev/AgentMaestro').exists())"
    },
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
    "git_log": {"repo_dir": "C:\\Dev\\AgentMaestro", "max_count": 5, "timeout_ms": 30000},
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
        "cmd": [
            "cmd",
            "/C",
            "echo RUN_COMMAND_SMOKE_OK && dir C:\\Dev\\AgentMaestro\\toolrunner\\app\\tools",
        ],
        "cwd": ".",
    },
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
    "edit_scheduled_task": {
        "scheduled_task_id": "scheduled-task-id-from-list",
        "title": "daily repo backup summary",
        "enabled": True,
        "timezone": "America/New_York",
        "local_time": "05:30",
        "execution_payload": {
            "objective": "Create a backup commit for the repository and summarize the last 24 hours of work.",
            "repo_dir": "C:/Dev/AgentMaestro",
        },
    },
    "disable_scheduled_task": {"scheduled_task_id": "scheduled-task-id-from-list"},
    "enable_scheduled_task": {"scheduled_task_id": "scheduled-task-id-from-list"},
    GOOGLE_BRIDGE_TOOL_NAME: deepcopy(GOOGLE_BRIDGE_TOOL_EXAMPLES),
    "list_scheduled_tasks": {"enabled_only": True, "limit": 10},
    "spawn_subrun": {
        "input_text": "Research the current weather outlook for Ocala tennis conditions and return a concise summary.",
        "metadata": {"purpose": "focused research", "topic": "weather"},
        "join_policy": "WAIT_ALL",
        "failure_policy": "IGNORE_FAILURE",
    },
    "get_current_datetime": {},
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
        {
            "tool": "ruff",
            "cwd": "C:\\Dev\\AgentMaestro",
            "paths": ["C:\\Dev\\AgentMaestro\\backend\\tools"],
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
    "search_files": {
        "tool": "The tool name.",
        "compact": "Whether compact mode was requested. When true, the compact envelope is returned and legacy top-level fields are not included.",
        "query": "The search text, glob, or regex that was searched.",
        "scope": "Normalized root/path scope used for the search.",
        "items": "Standardized match records with path, name, kind, and score. Ordering is stable and sorted by score, then path.",
        "returned_count": "Number of items actually returned in `items`.",
        "max_results_used": "The effective max_results limit applied to this result.",
        "selection": "Primary selected record when the tool has one.",
        "selection_excerpt": "Optional line-numbered excerpt text for the selected record.",
        "stats": "File and directory counts, exclusions, and allowed roots used by policy.",
        "truncated": "True when max_results, max_depth, or timeout limits stopped the scan.",
    },
    "list_symbols": {
        "tool": "The tool name.",
        "compact": "Whether compact mode was requested. When true, the compact envelope is returned and legacy top-level fields are not included.",
        "query": "The scanned path or directory.",
        "scope": "Normalized root/path scope used for the scan.",
        "items": "Standardized symbol records with path, name, kind, line, column, container, and signature. Ordering is stable and grouped by path.",
        "returned_count": "Number of items actually returned in `items`.",
        "max_results_used": "The effective max_results limit applied to this result.",
        "selection": "Primary selected symbol record when the tool has one. Compact mode trims this to a short summary with path, name, kind, line, column, container/scope, signature, and score when available.",
        "selection_excerpt": "Optional line-numbered excerpt around the selected record.",
        "stats": "File counts, symbol counts, exclusions, and allowed roots used by policy.",
        "truncated": "True when max_results, max_depth, or timeout limits stopped the scan.",
    },
    "find_symbol": {
        "tool": "The tool name.",
        "compact": "Whether compact mode was requested. When true, the compact envelope is returned and legacy top-level fields are not included.",
        "query": "The requested symbol name (`symbol_name`).",
        "scope": "Normalized root/path scope used for the lookup.",
        "items": "Standardized symbol records with path, name, kind, line, column, container, score, and signature. Ordering is stable and exact matches are ranked before fuzzy matches.",
        "returned_count": "Number of items actually returned in `items`.",
        "max_results_used": "The effective max_results limit applied to this result.",
        "selection": "Primary selected symbol record when the tool has one. Compact mode trims this to a short summary with path, name, kind, line, column, container/scope, signature, and score when available.",
        "selection_excerpt": "Optional line-numbered excerpt around the selected definition in compact mode.",
        "stats": "Symbol counts and allowed roots used by policy.",
        "truncated": "True when max_results or timeout limits stopped the scan.",
    },
    "find_references": {
        "tool": "The tool name.",
        "compact": "Whether compact mode was requested. When true, the compact envelope is returned and legacy top-level fields are not included.",
        "query": "The searched symbol name.",
        "scope": "Normalized root/path scope used for the lookup.",
        "items": "Standardized reference records with path, name, kind, line, column, and a short line-numbered excerpt. Ordering is stable and follows scan order with deterministic sorting.",
        "returned_count": "Number of items actually returned in `items`.",
        "max_results_used": "The effective max_results limit applied to this result.",
        "selection": "Primary selected reference record when the tool has one.",
        "selection_excerpt": "Short line-numbered excerpt for the selected reference.",
        "stats": "Reference counts, exclusions, and allowed roots used by policy.",
        "truncated": "True when max_results or timeout limits stopped the scan.",
    },
    "jump_to_symbol": {
        "tool": "The tool name.",
        "compact": "Whether compact mode was requested. When true, the compact envelope is returned and legacy top-level fields are not included.",
        "query": "The requested symbol name.",
        "scope": "Normalized root/path scope used for the lookup.",
        "items": "Standardized candidate records with path, name, kind, line, column, container, score, signature, and a short line-numbered excerpt. Ordering is stable and the best match is first.",
        "returned_count": "Number of items actually returned in `items`.",
        "max_results_used": "The effective max_results limit applied to this result.",
        "selection": "The best matching definition returned by the lookup. Symbol selections include defining file, line, column, container/scope, and signature when available.",
        "selection_excerpt": "Short line-numbered excerpt around the selected definition.",
        "stats": "Candidate counts and allowed roots used by policy.",
        "truncated": "True when max_results or timeout limits stopped the scan.",
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
    GOOGLE_BRIDGE_TOOL_NAME: GOOGLE_BRIDGE_TOOL_RESPONSE_FIELDS,
    "list_scheduled_tasks": {
        "count": "Number of scheduled tasks returned.",
        "results": "Concise scheduled-task records ordered by next run time, including scheduled_task_id, execution mode, and run linkage.",
        "scheduled_task_id": "Stable task identifier included in each result row for follow-up edit, disable, or enable actions.",
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
    },
    "get_current_datetime": {
        "datetime": "ISO 8601 local datetime string in the Tango timezone.",
        "timezone": "The IANA timezone used to format the returned local datetime.",
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
        "repo_dir": "Repository path argument echoed back in the response.",
        "branch": "Current branch metadata including name, upstream, ahead/behind, head_oid, and detached.",
        "is_clean": "True when there are no staged, unstaged, conflict, or requested untracked changes.",
        "staged": "List of staged paths.",
        "unstaged": "List of unstaged paths.",
        "untracked": "List of untracked paths when include_untracked=true.",
        "conflicts": "List of conflicting paths.",
        "raw": "Raw git status stdout/stderr plus truncation flags.",
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
    "git_add": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "staged_paths": "Normalized relative paths passed to git add when the request targeted explicit files.",
        "raw": "Raw git add stdout/stderr plus truncation flags.",
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
    "git_push": {
        "repo_dir": "Repository path argument echoed back in the response.",
        "remote": "The remote name that was pushed to.",
        "ref": "The ref that was pushed.",
        "pushed": "True when the push command completed successfully.",
        "raw": "Raw git push stdout/stderr plus truncation flags.",
    },
    "coverage_runner": {
        "total_percent": "Overall measured coverage percentage from coverage.json.",
        "files": "Per-file coverage summaries extracted from coverage.json.",
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
    "run_command": {
        "exit_code": "The process exit code, or null if the process timed out.",
        "stdout": "Captured standard output text.",
        "stderr": "Captured standard error text.",
        "timed_out": "True when the process exceeded its timeout.",
        "timeout_source": "Which timeout setting was enforced for the command.",
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
}


_TOOL_ADDITIONAL_DOCS: Dict[str, str] = {
    "file_patch": "\n\nPATCH FORMAT NOTES:\n"
    "- `patch_unified` is required.\n"
    "- Provide a complete unified diff for a single target file.\n"
    "- Include `---` and `+++` file markers and at least one `@@` hunk header.\n"
    "- Hunk headers must use explicit, accurate unified diff ranges, for example `@@ -1,2 +1,2 @@` or `@@ -0,0 +1,3 @@`.\n"
    "- Shorthand headers like `@@ -1 +1 @@` are rejected. Even pure insertions must include counts.\n"
    "- Do not include `*** Begin Patch` / `*** End Patch` fences; this tool expects only unified diff text.\n",
    "search_files": "\n\nPATH NOTES:\n"
    "- `scope` is the canonical name for the search root.\n"
    "- `scope` may be omitted, repo-relative, or absolute.\n"
    "- scope may point to a file, a directory, or a repo root scope.\n"
    "- Repo-relative scopes resolve from the repository root when one is provided in policy context.\n"
    "- Absolute roots are permitted only when they fall under the allowed roots for the run.\n"
    "- Hidden files and directories are included unless excluded by the default ignore rules.\n"
    "- Test paths are included by default unless `include_tests=false` is set.\n"
    "- Use this when you need to locate files or directories by name or path. Do not use it for content search; use `search_code` instead. This tool matches names and paths only and does not search file contents.\n\n"
    "OUTPUT NOTES:\n"
    "- Set `compact=true` when you want the standardized envelope with `tool`, `compact`, `query`, `scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`.\n"
    "- Compact mode does not include legacy top-level fields.\n"
    "- `items` is stable and ordered by score, then path/name.\n\n"
    "WORKFLOW NOTES:\n"
    "- Use the navigation tools sequentially when precision matters. Wait for one result before issuing the next navigation call.\n\n"
    "QUERY NOTES:\n"
    "- `query` can be a literal filename, partial path, glob-style pattern, or regex.\n"
    "- This is a path/name search only; it does not inspect file contents.\n"
    "- Search one path/name query at a time. For unrelated targets, make separate calls.\n"
    "- Use `is_regex=true` for regex searches; use `|` for alternatives instead of the word `OR`, for example `code_navigation.py|run_command_safe`.\n"
    "- In regex mode, exact path/name hits still sort ahead of fuzzy or partial matches.\n",
    "list_symbols": "\n\nPATH NOTES:\n"
    "- `scope` is the canonical name for the symbol scope.\n"
    "- `scope` may be absolute or repo-relative.\n"
    "- `scope` may point to a file, a directory, or a repo root scope.\n"
    "- Repo-relative scopes resolve from the repository root when one is provided in policy context.\n"
    "- Hidden files and directories are included unless excluded by the default ignore rules.\n"
    "- Test paths are included by default unless `include_tests=false` is set.\n\n"
    "- Use this when you already know the file or subtree and want a symbol outline. Do not use it for text search; use `search_code` or `search_files` instead.\n\n"
    "OUTPUT NOTES:\n"
    "- Set `compact=true` when you want the standardized envelope with `tool`, `compact`, `query`, `scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`.\n"
    "- Compact mode does not include legacy top-level fields.\n"
    "- `items` is stable and ordered by path, then symbol order within each file.\n\n"
    "WORKFLOW NOTES:\n"
    "- Use the navigation tools sequentially when precision matters. Wait for one result before issuing the next navigation call.\n\n"
    "RESULT NOTES:\n"
    "- Each entry summarizes the symbols found in a single file and includes per-file symbol counts.\n"
    "- Use `include_docstrings=true` only when you want docstring summaries in the returned symbol records.",
    "find_symbol": "\n\nPATH NOTES:\n"
    "- `scope` is the canonical name for the lookup scope.\n"
    "- `scope` may be absolute or repo-relative.\n"
    "- `scope` may point to a file, a directory, or a repo root scope.\n"
    "- Repo-relative scopes resolve from the repository root when one is provided in policy context.\n"
    "- Hidden files and directories are included unless excluded by the default ignore rules.\n"
    "- Test paths are included by default unless `include_tests=false` is set.\n\n"
    "- Use this when you know the symbol name and want the definition. Do not use it for content search; use `search_code` instead.\n\n"
    "OUTPUT NOTES:\n"
    "- Set `compact=true` when you want the standardized envelope with `tool`, `compact`, `query`, `scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`.\n"
    "- Compact mode does not include legacy top-level fields.\n"
    "- `items` is stable and exact matches are ordered before fuzzy matches.\n\n"
    "WORKFLOW NOTES:\n"
    "- Use the navigation tools sequentially when precision matters. Wait for one result before issuing the next navigation call.\n\n"
    "RESULT NOTES:\n"
    "- The tool supports exact or fuzzy symbol matching and ranks exact matches above fuzzy matches when both are available.\n"
    "- Use `symbol_name` for the canonical argument name; legacy callers may still send `name`.\n",
    "find_references": "\n\nPATH NOTES:\n"
    "- `scope` is the canonical name for the reference scope.\n"
    "- `scope` may be absolute or repo-relative.\n"
    "- `scope` may point to a file, a directory, or a repo root scope.\n"
    "- Repo-relative scopes resolve from the repository root when one is provided in policy context.\n"
    "- Hidden files and directories are included unless excluded by the default ignore rules.\n"
    "- Test paths are included by default unless `include_tests=false` is set.\n\n"
    "- Use this when you want to understand impact before changing a symbol. Do not use it for content search; use `search_code` instead.\n\n"
    "OUTPUT NOTES:\n"
    "- Set `compact=true` when you want the standardized envelope with `tool`, `compact`, `query`, `scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`.\n"
    "- Compact mode does not include legacy top-level fields.\n"
    "- `items` is stable and ordered by deterministic scan order.\n\n"
    "WORKFLOW NOTES:\n"
    "- Use the navigation tools sequentially when precision matters. Wait for one result before issuing the next navigation call.\n\n"
    "RESULT NOTES:\n"
    "- Reference hits include the file path, line, column, reference kind, and nearby context.\n"
    "- `context_lines` controls the amount of surrounding source shown for each hit.",
    "jump_to_symbol": "\n\nPATH NOTES:\n"
    "- `scope` is the canonical name for the jump scope.\n"
    "- `scope` may be absolute or repo-relative.\n"
    "- `scope` may point to a file, a directory, or a repo root scope.\n"
    "- Repo-relative scopes resolve from the repository root when one is provided in policy context.\n"
    "- Hidden files and directories are included unless excluded by the default ignore rules.\n"
    "- Test paths are included by default unless `include_tests=false` is set.\n\n"
    "- Use this when you want the best definition plus a small excerpt. Do not use it for content search; use `search_code` instead.\n\n"
    "OUTPUT NOTES:\n"
    "- Set `compact=true` when you want the standardized envelope with `tool`, `compact`, `query`, `scope`, `items`, `returned_count`, `max_results_used`, `selection`, `selection_excerpt`, `stats`, and `truncated`.\n"
    "- Compact mode does not include legacy top-level fields.\n"
    "- `items` is stable and the best match appears first.\n\n"
    "WORKFLOW NOTES:\n"
    "- Use the navigation tools sequentially when precision matters. Wait for one result before issuing the next navigation call.\n\n"
    "RESULT NOTES:\n"
    "- The tool returns the best symbol match plus an excerpt around that definition.\n"
    "- Use `context_lines` to control how many surrounding lines appear in the excerpt.",
    "search_code": "\n\nPATH NOTES:\n"
    "- `root` may be omitted, repo-relative, or absolute.\n"
    "- Repo-relative `root` values resolve from the repository root when one is provided in policy context.\n"
    "- Absolute `root` values are permitted only when they fall under the allowed roots for the run.\n"
    "- `include_globs` are evaluated relative to the provided `root`.\n\n"
    "ROOT NOTES:\n"
    "- `absolute_root` is optional on path-scoped tools.\n"
    "- Omit `absolute_root` unless you truly need an absolute override.\n"
    "- Do not pass an empty string; empty values are treated as omitted.\n\n"
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
    "- For Ruff-style runs (`ruff_format`), omit `cmd` entirely and use `paths` plus optional `args` only.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff_format` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.\n",
    "lint_runner": "\n\nRUN MODE NOTES:\n"
    "- `tool` is required.\n"
    "- Supported values are `ruff`, `flake8`, `eslint`, `prettier`, and `command`.\n"
    "- `cwd` and each item in `paths` may be absolute or repo-relative.\n"
    "- For Ruff-style runs (`ruff`), omit `cmd` entirely and use `paths` plus optional `args` only.\n"
    "- Use `cmd` only when `tool=command`.\n\n"
    "PYTHON ENVIRONMENT NOTES:\n"
    "- `ruff` runs through `TOOLRUNNER_PYTHON`.\n"
    "- If `TOOLRUNNER_PYTHON` is unset, toolrunner falls back to `.venv` discovery before using plain `python`.\n"
    "- If `ruff` is missing, the tool reports the resolved interpreter path and source.\n",
    "run_command_safe": "\n\nSAFE COMMAND NOTES:\n"
    "- `argv` is required and must be a list of strings.\n"
    "- Allowed executables are limited to `python`, `pytest`, `ruff`, `mypy`, `uv`, and `django-admin`.\n"
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
    "- Pass the executable and each argument as separate list items.\n"
    "- If you need shell features such as `&&`, invoke a shell explicitly.\n"
    "- Use `run_command` only as a last resort when no specialized tool matches the task.\n"
    "- Do not use `run_command` for Git operations that map to `git_add`, `git_status`, `git_diff`, `git_log`, `git_apply`, `git_commit`, `git_push`, `git_checkout`, or `git_branch_create`.\n"
    "- Do not use `run_command` for direct file content reads that should go through `file_read`.\n",
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
    "- Scheduled work runs headlessly. Use `task_type=other_task` and put structured intent in `execution_payload`.\n"
    "- Scheduled runs inherit the agent's backup models and retry policy, so backup failover applies automatically.\n"
    "- Prefer `recurrence` for anything more complex than a single daily wall-clock time.\n"
    "- If a timezone argument is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC.\n"
    "- If the schedule depends on a relative date like tomorrow or next Friday and the current local date is not already known, call `get_current_datetime` first and anchor the schedule in that local time.\n"
    "- Use `title` and `execution_payload` to describe the recurring job clearly so the future headless run has enough context.\n"
    "- list_scheduled_tasks already returns scheduled_task_id, so use that identifier for future edit, disable, or enable operations.\n",
    "edit_scheduled_task": "\n\nSCHEDULING NOTES:\n"
    "- `edit_scheduled_task` updates an existing scheduled task without creating a new one.\n"
    "- Use `scheduled_task_id` from `list_scheduled_tasks` or `schedule_task` to target the task.\n"
    "- You may update the title, recurrence, timezone, local_time, enabled state, delivery target, or execution payload.\n"
    "- If a timezone argument is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC.\n",
    "disable_scheduled_task": "\n\nSCHEDULING NOTES:\n"
    "- `disable_scheduled_task` is the soft-delete path for scheduled tasks.\n"
    "- It sets `enabled=false` and preserves the existing task record for later re-enabling.\n"
    "- Use `scheduled_task_id` from `list_scheduled_tasks` or `schedule_task`.\n",
    "enable_scheduled_task": "\n\nSCHEDULING NOTES:\n"
    "- `enable_scheduled_task` reactivates a disabled task and recomputes its next run time from the stored recurrence rule.\n"
    "- Use `scheduled_task_id` from `list_scheduled_tasks` or `schedule_task`.\n",
    GOOGLE_BRIDGE_TOOL_NAME: "\n\nGOOGLE BRIDGE NOTES:\n"
    "- Use this tool for direct Google Gmail/Calendar reads and writes from the agent, plus Gmail draft/send/trash/delete workflows.\n"
    "- The payload is JSON-in / JSON-out and matches the same bridge contract used by scheduled headless runs.\n"
    "- Current execution supports Gmail reads, Gmail draft/send/trash/delete workflows, and Calendar create, update, and delete workflows are supported. For Gmail list reads, a bare list defaults to unread messages. Use include_read=true when you want all mail, or provide a query/label filter for a narrower mailbox view. Gmail list/read query filters support exact sender, sender domain, subject, and top-level OR splitting across those clauses. Use account_scope=all when you want the bridge to fan out across every active connected account and merge the results. For Gmail trash/delete queries, use account_scope=all when you want the same query to apply across every connected account. For Calendar list reads, omit calendar_id or use all to query every calendar on the connected account; primary stays available when you explicitly want one calendar. Future Google surfaces can reuse the same tool name and payload contract.\n"
    "- For Gmail reads, use query filters such as from:info@airbnb.com for exact sender, from:airbnb.com for sender-domain, subject:(\"Airbnb\") for subject search, plus label_ids or include_read for mailbox filtering. For Gmail trash/delete, never use read as a lookup step. OR is supported for Gmail list/read searches and bulk trash/delete cleanup only at the top level, where it is split into separate Gmail clauses before merging or deleting. Nested OR inside parentheses is rejected as malformed. For bulk cleanup, choose the Gmail query shape that matches your intent: subject:(\"Airbnb\") for subject-based cleanup, from:info@airbnb.com for exact sender cleanup, and from:airbnb.com for sender-domain cleanup. If you need multiple cleanup targets in one call, join them with OR and the bridge will split them into separate Gmail clauses as long as each clause is complete. For Gmail writes and Calendar writes, if a timezone argument is omitted, the bridge assumes the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` rather than UTC. If a date depends on 'tomorrow', 'next Friday', or another relative expression and the current local date is not already known, call `get_current_datetime` first and anchor the schedule in that local time. For Gmail writes, create a draft first and then send it when ready. The preferred pattern in each case is action_kind=delete with operation=trash (or omit operation and let it default) plus the matching Gmail query. Trash is the safe default; set delete_mode=delete only when you explicitly want permanent deletion. Gmail OR fan-out is capped at 10 top-level clauses by default; set TOOLRUNNER_GMAIL_OR_CLAUSE_LIMIT to adjust it. If the agent accidentally writes `from:@domain.com` or adds stray spaces after query tokens, the bridge normalizes that to the canonical Gmail form.\n"
    "- Use `steps` for ordered multi-step plans when you need to combine multiple Google reads in one call.\n",
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
    "- The default `join_policy` is `wait_all` and the default `failure_policy` is `fail_fast`.\n",
    "get_current_datetime": "\n\nTIME NOTES:\n"
    "- `get_current_datetime` takes no arguments.\n"
    "- It returns the current local datetime in the Tango timezone as an ISO 8601 string with offset.\n"
    "- The Tango timezone defaults to `America/New_York` and can be overridden with `TANGO_TIME_ZONE`.\n",
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
