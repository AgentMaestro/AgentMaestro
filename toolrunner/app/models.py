from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

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

    @field_validator("run_id", "workspace_id", "request_id")
    def required_ids(cls, value: str) -> str:
        if not value:
            raise ValueError("identifier fields must be provided")
        return value


class ShellArgs(BaseModel):
    cmd: List[str]
    cwd: str = Field(default=".")
    env: Dict[str, str] | None = None

    @field_validator("cmd")
    def ensure_command_not_empty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("cmd must include at least one element")
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

    @model_validator(mode="after")
    def ensure_code_and_entrypoint(self) -> "PythonArgs":
        if self.entrypoint and not self.files:
            raise ValueError("files must be provided when using entrypoint")
        if not self.code and not self.entrypoint:
            raise ValueError("either code or entrypoint must be provided")
        return self


MAX_REPO_TREE_ENTRIES = 5000
DEFAULT_REPO_TREE_EXCLUDES = (
    "**/.git/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/__pycache__/**",
)
DEFAULT_SEARCH_EXCLUDES = (
    "**/.git/**",
    "**/.venv/**",
    "**/node_modules/**",
)


class FileReadArgs(BaseModel):
    path: str
    mode: Literal["text", "binary"] = "text"
    encoding: str = "utf-8"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    max_bytes: int = Field(default=262144, gt=0)

    @field_validator("path")
    def normalize_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("absolute paths not allowed")
        return value.replace("\\", "/")

    @field_validator("start_line", "end_line")
    def positive_line(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("line numbers must be 1-based positive")
        return value

    @field_validator("end_line", mode="after")
    def end_after_start(
        cls, value: Optional[int], info: ValidationInfo
    ) -> Optional[int]:
        start = info.data.get("start_line")
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

    @field_validator("path")
    def normalize_path(cls, value: str) -> str:
        if value.startswith("/"):
            raise ValueError("absolute paths not allowed")
        return value.replace("\\", "/")

    @model_validator(mode="after")
    def ensure_content_for_mode(self) -> "FileWriteArgs":
        if self.mode == "text" and self.content is None:
            raise ValueError("content is required for text mode")
        if self.mode == "binary" and self.content_base64 is None:
            raise ValueError("content_base64 is required for binary mode")
        return self


class FilePatchArgs(BaseModel):
    path: str
    patch_unified: str
    strip_prefix: int = Field(default=0, ge=0)
    fail_on_reject: bool = True
    expected_sha256: Optional[str] = None
    create_if_missing: bool = False
    backup: bool = True

    @field_validator("path")
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

    @field_validator("root")
    def normalize_root(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute paths not allowed")
        result = candidate.as_posix()
        return result or "."

    @field_validator("exclude_globs", "include_globs", mode="before")
    def normalize_globs(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        return [pattern.replace("\\", "/") for pattern in value]

    @field_validator("max_entries")
    def cap_max_entries(cls, value: int) -> int:
        return min(value, MAX_REPO_TREE_ENTRIES)


class SearchCodeArgs(BaseModel):
    query: str = Field(..., min_length=1)
    is_regex: bool = False
    case_sensitive: bool = False
    root: str = "."
    include_globs: List[str] | None = None
    exclude_globs: List[str] = Field(default_factory=lambda: list(DEFAULT_SEARCH_EXCLUDES))
    max_results: int = Field(default=100, ge=1)
    max_matches_per_file: int = Field(default=20, ge=1)
    context_lines: int = Field(default=2, ge=0)
    timeout_ms: int = Field(default=3000, ge=0)

    @field_validator("root")
    def normalize_root(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute paths not allowed")
        result = candidate.as_posix()
        return result or "."

    @field_validator("include_globs", "exclude_globs", mode="before")
    def normalize_globs(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        return [pattern.replace("\\", "/") for pattern in value]


class RunCommandArgs(BaseModel):
    cmd: List[str]
    cwd: str = "."
    env: Dict[str, str] | None = None
    timeout_ms: int = Field(default=300_000, ge=0, le=3_600_000)
    max_output_bytes: int = Field(default=262_144, ge=1, le=2_097_152)
    stdin_text: str | None = None

    @field_validator("cmd")
    def cmd_must_not_be_empty(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("cmd must include at least one element")
        return value

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return candidate.as_posix()


class RunnerTestArgs(BaseModel):
    kind: Literal["powershell_script", "pytest", "command"]
    script_path: str | None = None
    script_args: List[str] = Field(default_factory=list)
    pytest_args: List[str] | None = None
    cmd: List[str] | None = None
    cwd: str = "."
    env: Dict[str, str] | None = None
    timeout_ms: int = Field(default=600_000, ge=0)
    max_output_bytes: int = Field(default=524_288, ge=1)
    parse: Literal["pytest", "none"] = "pytest"

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        if Path(normalized).is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return normalized

    @field_validator("script_path")
    def normalize_script_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.replace("\\", "/")
        if Path(normalized).is_absolute():
            raise ValueError("absolute paths not allowed")
        return normalized

    @model_validator(mode="after")
    def ensure_kind_fields(self) -> "RunnerTestArgs":
        if self.kind == "powershell_script" and not self.script_path:
            raise ValueError("script_path is required when kind is powershell_script")
        if self.kind == "pytest" and not self.pytest_args:
            raise ValueError("pytest_args are required when kind is pytest")
        if self.kind == "command" and not self.cmd:
            raise ValueError("cmd is required when kind is command")
        return self


class LintArgs(BaseModel):
    tool: Literal["ruff", "flake8", "eslint", "prettier", "command"]
    cwd: str = "."
    paths: List[str] | None = None
    args: List[str] | None = None
    cmd: List[str] | None = None
    timeout_ms: int = Field(default=180_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)
    parse: Literal["ruff", "flake8", "eslint", "none"] | None = None

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return candidate.as_posix()

    @field_validator("paths", mode="before")
    def normalize_paths(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        normalized: List[str] = []
        for item in value:
            if item.startswith("/"):
                raise ValueError("absolute paths not allowed")
            normalized.append(item.replace("\\", "/"))
        return normalized

    @model_validator(mode="after")
    def ensure_fields(self) -> "LintArgs":
        if self.tool == "command" and not self.cmd:
            raise ValueError("cmd is required when tool=command")
        if self.tool != "command" and self.cmd:
            raise ValueError("cmd may only be specified when tool=command")
        if self.parse is None:
            if self.tool in ("ruff", "flake8", "eslint"):
                self.parse = self.tool
            else:
                self.parse = "none"
        return self


class TypecheckArgs(BaseModel):
    tool: Literal["mypy", "pyright", "tsc", "command"]
    cwd: str = "."
    args: List[str] | None = None
    cmd: List[str] | None = None
    timeout_ms: int = Field(default=300_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)
    parse: Literal["mypy", "pyright", "tsc", "none"] | None = None

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return candidate.as_posix()

    @model_validator(mode="after")
    def ensure_fields(self) -> "TypecheckArgs":
        if self.tool == "command" and not self.cmd:
            raise ValueError("cmd is required when tool=command")
        if self.tool != "command" and self.cmd:
            raise ValueError("cmd may only be specified when tool=command")
        if self.parse is None:
            if self.tool == "command":
                self.parse = "none"
            elif self.tool in ("pyright", "mypy", "tsc"):
                self.parse = self.tool
            else:
                self.parse = "none"
        return self


class FormatArgs(BaseModel):
    tool: Literal["ruff_format", "black", "prettier", "command"]
    mode: Literal["check", "apply"] = "check"
    cwd: str = "."
    paths: List[str] | None = None
    args: List[str] | None = None
    cmd: List[str] | None = None
    timeout_ms: int = Field(default=180_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return candidate.as_posix()

    @field_validator("paths", mode="before")
    def normalize_paths(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        normalized: List[str] = []
        for item in value:
            if item.startswith("/"):
                raise ValueError("absolute paths not allowed")
            normalized.append(item.replace("\\", "/"))
        return normalized

    @model_validator(mode="after")
    def ensure_fields(self) -> "FormatArgs":
        if self.tool == "command" and not self.cmd:
            raise ValueError("cmd is required when tool=command")
        if self.tool != "command" and self.cmd:
            raise ValueError("cmd may only be specified when tool=command")
        return self


class CoverageArgs(BaseModel):
    kind: Literal["pytest_coverage"]
    cwd: str = "."
    args: List[str] | None = None
    timeout_ms: int = Field(default=600_000, ge=0)
    max_output_bytes: int = Field(default=524_288, ge=1)

    @field_validator("cwd")
    def normalize_cwd(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute cwd paths not allowed")
        return candidate.as_posix()


class GitDiffArgs(BaseModel):
    repo_dir: str = "."
    staged: bool = False
    paths: List[str] | None = None
    context_lines: int = Field(default=3, ge=0)
    detect_renames: bool = True
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=524_288, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()

    @field_validator("paths", mode="before")
    def normalize_paths(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        normalized: List[str] = []
        for item in value:
            if item.startswith("/"):
                raise ValueError("absolute paths not allowed")
            normalized.append(item.replace("\\", "/"))
        return normalized


class GitBranchCreateArgs(BaseModel):
    repo_dir: str = "."
    name: str
    start_point: str = "HEAD"
    checkout: bool = True
    force: bool = False
    timeout_ms: int = Field(default=120_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()


class GitAddArgs(BaseModel):
    repo_dir: str = "."
    paths: List[str] | None = None
    all: bool = False
    intent_to_add: bool = False
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()

    @field_validator("paths", mode="before")
    def normalize_paths(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        normalized: List[str] = []
        for item in value:
            if item.startswith("/"):
                raise ValueError("absolute paths not allowed")
            normalized.append(item.replace("\\", "/"))
        return normalized

    @model_validator(mode="after")
    def ensure_combinations(self) -> "GitAddArgs":
        if self.all and (self.paths or self.intent_to_add):
            raise ValueError("all=True cannot be combined with paths or intent_to_add")
        if self.intent_to_add and not self.paths:
            raise ValueError("intent_to_add requires paths")
        return self


class GitPushArgs(BaseModel):
    repo_dir: str = "."
    remote: str = "origin"
    ref: str = Field(..., min_length=1)
    set_upstream: bool = True
    force: bool = False
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()


class GitStatusArgs(BaseModel):
    repo_dir: str = "."
    porcelain: Literal["v1", "v2"] = "v2"
    include_untracked: bool = True
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)


class GitApplyArgs(BaseModel):
    repo_dir: str = "."
    patch_unified: str = Field(..., min_length=1)
    strip_prefix: int = Field(default=1, ge=0)
    reject: bool = True
    check: bool = False
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)
    include_untracked: bool = True
    timeout_ms: int = Field(default=30_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()


class GitCheckoutArgs(BaseModel):
    repo_dir: str = "."
    ref: str
    create: bool = False
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()

    @field_validator("ref")
    def ensure_ref(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("ref is required")
        return value.strip()


class GitCommitArgs(BaseModel):
    repo_dir: str = "."
    message: str
    paths_to_add: List[str] | None = None
    add_all: bool = False
    signoff: bool = False
    amend: bool = False
    timeout_ms: int = Field(default=60_000, ge=0)
    max_output_bytes: int = Field(default=262_144, ge=1)

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()

    @field_validator("message")
    def ensure_message(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("message is required")
        return value.strip()

    @field_validator("paths_to_add", mode="before")
    def normalize_paths(cls, value: List[str] | None) -> List[str] | None:
        if value is None:
            return None
        normalized: List[str] = []
        for item in value:
            if item.startswith("/"):
                raise ValueError("absolute paths not allowed")
            normalized.append(item.replace("\\", "/"))
        return normalized


class GitLogArgs(BaseModel):
    repo_dir: str = "."
    max_count: int = Field(default=20, ge=1)
    ref: str = "HEAD"

    @field_validator("repo_dir")
    def normalize_repo_dir(cls, value: str) -> str:
        normalized = value.replace("\\", "/") or "."
        candidate = Path(normalized)
        if candidate.is_absolute():
            raise ValueError("absolute repo_dir paths not allowed")
        return candidate.as_posix()

    @field_validator("ref")
    def ensure_ref(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("ref is required")
        return value.strip()
