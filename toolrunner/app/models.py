from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator

from .config import COMMAND_TIMEOUT, OUTPUT_LIMIT


class ExecuteLimits(BaseModel):
    timeout_s: int = Field(default=COMMAND_TIMEOUT, ge=1)
    max_output_bytes: int = Field(default=OUTPUT_LIMIT, ge=1)


class ExecuteRequest(BaseModel):
    request_id: str
    workspace_id: str
    run_id: str
    tool_name: Literal["shell_exec", "python_exec"]
    args: Dict[str, Any] = Field(default_factory=dict)
    policy: Dict[str, Any] | None = None
    limits: ExecuteLimits = Field(default_factory=ExecuteLimits)

    @validator("run_id", "workspace_id", "request_id")
    def required_ids(cls, value: str) -> str:
        if not value:
            raise ValueError("identifier fields must be provided")
        return value


class ShellArgs(BaseModel):
    cmd: List[str]
    cwd: str = Field(default=".")
    env: Dict[str, str] | None = None

    @validator("cmd")
    def ensure_command_not_empty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("cmd must include at least one element")
        return value


class PythonFileItem(BaseModel):
    path: str
    content_b64: str


class PythonArgs(BaseModel):
    code: Optional[str] = None
    files: List[PythonFileItem] = Field(default_factory=list)
    entrypoint: Optional[str] = None

    @validator("entrypoint")
    def ensure_entrypoint_with_files(cls, value: str | None, values: Dict[str, Any]) -> str | None:
        if value and not values.get("files"):
            raise ValueError("files must be provided when using entrypoint")
        return value

    @validator("code")
    def ensure_code_or_entrypoint(cls, value: str | None, values: Dict[str, Any]) -> str | None:
        if not value and not values.get("entrypoint"):
            raise ValueError("either code or entrypoint must be provided")
        return value


class ExecuteResponse(BaseModel):
    request_id: str
    status: Literal["COMPLETED", "FAILED"]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    result: Dict[str, Any] | None = None


class PythonFileItem(BaseModel):
    path: str
    content_b64: str


class PythonArgs(BaseModel):
    code: Optional[str] = None
    files: List[PythonFileItem] = Field(default_factory=list)
    entrypoint: Optional[str] = None

    @validator("entrypoint")
    def ensure_entrypoint_with_files(cls, value: str | None, values: Dict[str, Any]) -> str | None:
        if value and not values.get("files"):
            raise ValueError("files must be provided when using entrypoint")
        return value

    @validator("code")
    def ensure_code_or_entrypoint(cls, value: str | None, values: Dict[str, Any]) -> str | None:
        if not value and not values.get("entrypoint"):
            raise ValueError("either code or entrypoint must be provided")
        return value


MAX_REPO_TREE_ENTRIES = 5000
DEFAULT_REPO_TREE_EXCLUDES = (
    "**/.git/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/__pycache__/**",
)


class FileReadArgs(BaseModel):
    path: str
    mode: Literal["text", "binary"] = "text"
    encoding: str = "utf-8"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    max_bytes: int = Field(default=262144, gt=0)

    @validator("path")
    def normalize_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("absolute paths not allowed")
        return value.replace("\\", "/")

    @validator("start_line", "end_line")
    def positive_line(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("line numbers must be 1-based positive")
        return value

    @validator("end_line")
    def end_after_start(cls, value: Optional[int], values: Dict[str, Any]) -> Optional[int]:
        start = values.get("start_line")
        if value is not None and start and value < start:
            raise ValueError("end_line must be >= start_line")
        return value


class FileWriteArgs(BaseModel):
    path: str
    mode: Literal["text", "binary"] = "text"
    content: Optional[str] = None
    content_base64: Optional[str] = None
    encoding: str = "utf-8"
    overwrite: bool = False
    make_dirs: bool = True
    atomic: bool = True
    expected_sha256: Optional[str] = None

    @validator("path")
    def normalize_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("absolute paths not allowed")
        return value.replace("\\", "/")

    @validator("content")
    def text_requires_content(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if values.get("mode") == "text" and value is None:
            raise ValueError("content is required for text mode")
        return value

    @validator("content_base64")
    def binary_requires_content(cls, value: Optional[str], values: Dict[str, Any]) -> Optional[str]:
        if values.get("mode") == "binary" and value is None:
            raise ValueError("content_base64 is required for binary mode")
        return value


class FilePatchArgs(BaseModel):
    path: str
    patch_unified: str
    strip_prefix: int = Field(default=0, ge=0)
    fail_on_reject: bool = True
    expected_sha256: Optional[str] = None
    create_if_missing: bool = False
    backup: bool = True

    @validator("path")
    def normalize_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("absolute paths not allowed")
        return value.replace("\\", "/")


class RepoTreeArgs(BaseModel):
    root: str = "."
    max_depth: int = Field(default=6, ge=0)
    include_files: bool = True
    include_dirs: bool = True
    follow_symlinks: bool = False
    exclude_globs: List[str] = Field(default_factory=lambda: list(DEFAULT_REPO_TREE_EXCLUDES))
    include_globs: List[str] | None = None
    max_entries: int = Field(default=MAX_REPO_TREE_ENTRIES, ge=1)
    include_metadata: bool = True

    @validator("root")
    def normalize_root(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute paths not allowed")
        result = candidate.as_posix()
        return result or "."

    @validator("exclude_globs", "include_globs", pre=True)
    def normalize_globs(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        return [pattern.replace("\\", "/") for pattern in value]

    @validator("max_entries")
    def cap_max_entries(cls, value: int) -> int:
        return min(value, MAX_REPO_TREE_ENTRIES)
