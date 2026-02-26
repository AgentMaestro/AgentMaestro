from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError, SchemaError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / ".agentmaestro" / "schemas"


class SchemaValidationError(ValueError):
    def __init__(self, schema_name: str, errors: list[str]):
        self.schema_name = schema_name
        self.errors = errors
        message = f"{schema_name} validation failed ({len(errors)} error(s)): {errors}"
        super().__init__(message)


def _schema_path(schema_name: str) -> Path:
    return SCHEMA_DIR / f"{schema_name}.schema.json"


@cache
def _load_schema(schema_name: str) -> dict[str, Any]:
    path = _schema_path(schema_name)
    if not path.exists():
        raise FileNotFoundError(f"schema {schema_name} not found at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SchemaError(f"failed to parse schema {path}: {exc}") from exc


def _format_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.path) or "<root>"
    return f"{path}: {error.message}"


def _validate(schema_name: str, data: Any) -> None:
    schema = _load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: tuple(err.path))
    if errors:
        raise SchemaValidationError(schema_name, [_format_error(err) for err in errors])


def validate_run_charter(data: Any) -> None:
    _validate("run_charter", data)


def validate_plan(data: Any) -> None:
    _validate("plan", data)


def validate_step_report(data: Any) -> None:
    _validate("step_report", data)


def validate_tool_call_envelope(data: Any) -> None:
    _validate("tool_call_envelope", data)


__all__ = [
    "SchemaValidationError",
    "validate_run_charter",
    "validate_plan",
    "validate_step_report",
    "validate_tool_call_envelope",
]
