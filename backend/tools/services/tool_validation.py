from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from tools.models import ToolDefinition


def _normalize_text(value: object | None) -> str:
    return str(value or "").strip()


def _is_missing(value: object | None) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return False
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _required_parameters_from_definition(definition: ToolDefinition) -> list[str]:
    tool = getattr(definition, "tool", None)
    schema = deepcopy(definition.args_schema or getattr(tool, "args_schema", {}) or {})
    required = list(getattr(tool, "required_parameters", None) or schema.get("required") or [])
    return [_normalize_text(value) for value in required if _normalize_text(value)]


@dataclass(frozen=True)
class ToolArgumentValidationError(ValueError):
    tool_name: str
    required_parameters: list[str]
    missing_parameters: list[str]
    submitted_args: dict[str, Any]
    args_schema: dict[str, Any]

    def __str__(self) -> str:
        submitted = json.dumps(self.submitted_args, ensure_ascii=False, sort_keys=True, default=str)
        required = json.dumps(self.required_parameters, ensure_ascii=False)
        missing = json.dumps(self.missing_parameters, ensure_ascii=False)
        schema = json.dumps(self.args_schema, ensure_ascii=False, sort_keys=True, default=str)
        return (
            f"Tool '{self.tool_name}' argument validation failed. "
            f"Submitted args: {submitted}. "
            f"This tool requires parameters: {required}. "
            f"Missing required parameters: {missing}. "
            f"Tool schema: {schema}."
        )

    def to_result(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": "tool_runner.MISSING_REQUIRED_ARGUMENTS",
                "message": str(self),
                "details": {
                    "tool_name": self.tool_name,
                    "submitted_args": self.submitted_args,
                    "required_parameters": self.required_parameters,
                    "missing_parameters": self.missing_parameters,
                    "args_schema": self.args_schema,
                },
            },
        }


def validate_required_tool_arguments(
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    definition: ToolDefinition | None = None,
) -> None:
    if definition is None:
        return
    submitted_args = dict(args or {})
    required_parameters = _required_parameters_from_definition(definition)
    if not required_parameters:
        return
    missing_parameters = [name for name in required_parameters if _is_missing(submitted_args.get(name))]
    if not missing_parameters:
        return
    raise ToolArgumentValidationError(
        tool_name=tool_name,
        required_parameters=required_parameters,
        missing_parameters=missing_parameters,
        submitted_args=submitted_args,
        args_schema=deepcopy(definition.args_schema or getattr(getattr(definition, "tool", None), "args_schema", {}) or {}),
    )
