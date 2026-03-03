from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def _schema(properties: Dict[str, Dict[str, Any]], *, required: List[str] | None = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": deepcopy(properties),
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _base_tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


REPO_TREE_PROPERTIES = {
    "root": {"type": "string"},
    "max_depth": {"type": "integer", "minimum": 0},
    "include_files": {"type": "boolean"},
    "include_dirs": {"type": "boolean"},
    "follow_symlinks": {"type": "boolean"},
    "exclude_globs": {"type": "array", "items": {"type": "string"}},
    "include_globs": {"type": "array", "items": {"type": "string"}},
    "max_entries": {"type": "integer", "minimum": 1},
    "include_metadata": {"type": "boolean"},
    "absolute_root": {"type": "string"},
}


SEARCH_CODE_PROPERTIES = {
    "query": {"type": "string"},
    "is_regex": {"type": "boolean"},
    "case_sensitive": {"type": "boolean"},
    "root": {"type": "string"},
    "absolute_root": {"type": "string"},
    "include_globs": {"type": "array", "items": {"type": "string"}},
    "exclude_globs": {"type": "array", "items": {"type": "string"}},
    "max_results": {"type": "integer", "minimum": 1},
    "max_matches_per_file": {"type": "integer", "minimum": 1},
    "context_lines": {"type": "integer", "minimum": 0},
    "timeout_ms": {"type": "integer", "minimum": 0},
}


FILE_READ_PROPERTIES = {
    "path": {"type": "string"},
    "mode": {"type": "string", "enum": ["text", "binary"]},
    "encoding": {"type": "string"},
    "start_line": {"type": "integer", "minimum": 1},
    "end_line": {"type": "integer", "minimum": 1},
    "max_bytes": {"type": "integer", "minimum": 1},
    "absolute_root": {"type": "string"},
}


FILE_WRITE_PROPERTIES = {
    "path": {"type": "string"},
    "mode": {"type": "string", "enum": ["text", "binary"]},
    "content": {"type": "string"},
    "content_base64": {"type": "string"},
    "encoding": {"type": "string"},
    "overwrite": {"type": "boolean"},
    "make_dirs": {"type": "boolean"},
    "atomic": {"type": "boolean"},
    "expected_sha256": {"type": "string"},
}


FILE_PATCH_PROPERTIES = {
    "path": {"type": "string"},
    "patch_unified": {"type": "string"},
    "strip_prefix": {"type": "integer", "minimum": 0},
    "fail_on_reject": {"type": "boolean"},
    "expected_sha256": {"type": "string"},
    "create_if_missing": {"type": "boolean"},
    "backup": {"type": "boolean"},
}


SHELL_EXEC_PROPERTIES = {
    "cmd": {"type": "array", "items": {"type": "string"}},
    "cwd": {"type": "string"},
    "env": {"type": "object", "additionalProperties": {"type": "string"}},
}


PYTHON_EXEC_PROPERTIES = {
    "code": {"type": "string"},
    "files": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content_b64": {"type": "string"},
            },
            "required": ["path", "content_b64"],
            "additionalProperties": False,
        },
    },
    "entrypoint": {"type": "string"},
}


_TOOL_SCHEMAS = [
    _base_tool(
        "repo_tree",
        "Walk a directory tree and report entries",
        _schema(REPO_TREE_PROPERTIES, required=["root"]),
    ),
    _base_tool(
        "search_code",
        "Search source files for a literal or regex query",
        _schema(SEARCH_CODE_PROPERTIES, required=["query"]),
    ),
    _base_tool(
        "file_read",
        "Read text or binary files",
        _schema(FILE_READ_PROPERTIES, required=["path"]),
    ),
    _base_tool(
        "file_write",
        "Write or overwrite a file",
        _schema(FILE_WRITE_PROPERTIES, required=["path"]),
    ),
    _base_tool(
        "file_patch",
        "Apply a unified diff patch",
        _schema(FILE_PATCH_PROPERTIES, required=["path", "patch_unified"]),
    ),
    _base_tool(
        "shell_exec",
        "Execute a shell command under the workspace",
        _schema(SHELL_EXEC_PROPERTIES, required=["cmd"]),
    ),
    _base_tool(
        "python_exec",
        "Run Python code or files",
        _schema(PYTHON_EXEC_PROPERTIES, required=["code"]),
    ),
]


_TOOL_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "repo_tree": {"root": ".", "max_depth": 3, "include_files": True, "absolute_root": "<ABSOLUTE_ROOT>"},
    "search_code": {
        "query": "AGENTMAESTRO_TOOLRUNNER_URL",
        "is_regex": False,
        "case_sensitive": False,
        "root": ".",
        "max_results": 5,
        "timeout_ms": 3000,
    },
    "file_read": {"path": "agentmaestro/settings/base.py", "mode": "text"},
    "file_write": {"path": "smoke/real_payload.json", "content": "probe", "overwrite": True, "make_dirs": True},
    "file_patch": {
        "path": "smoke/real_payload.json",
        "patch_unified": "--- a/smoke/real_payload.json\n+++ b/smoke/real_payload.json\n@@ -1 +1 @@\n- probe\n+ updated",
    },
    "shell_exec": {"cmd": ["powershell", "-NoProfile", "-Command", "echo probe"], "cwd": "."},
    "python_exec": {"code": "import hashlib\nprint(hashlib.sha256(b'AgentMaestro').hexdigest())"},
}


def get_tool_schemas() -> List[Dict[str, Any]]:
    return deepcopy(_TOOL_SCHEMAS)


def get_tool_arg_templates() -> Dict[str, Dict[str, Any]]:
    return deepcopy(_TOOL_TEMPLATES)
