from __future__ import annotations

import re
from typing import Iterable

from fastapi.responses import JSONResponse

from ..config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE

_MISSING_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError:\s*)?No module named ['\"]?(?P<module>[A-Za-z0-9_.-]+)"
)


def detect_missing_python_module(result: dict[str, object], expected_modules: Iterable[str]) -> str | None:
    expected = {name.strip() for name in expected_modules if name and name.strip()}
    if not expected:
        return None
    combined = "\n".join(
        str(part or "")
        for part in (result.get("stderr"), result.get("stdout"))
    )
    for match in _MISSING_MODULE_RE.finditer(combined):
        module_name = match.group("module").strip()
        top_level = module_name.split(".", 1)[0]
        if module_name in expected or top_level in expected:
            return top_level
    return None


def missing_python_module_response(
    *,
    tool_name: str,
    module_name: str,
    result: dict[str, object],
    details: dict[str, object] | None = None,
) -> JSONResponse:
    error_details = {
        "tool": tool_name,
        "missing_module": module_name,
        "python_interpreter": PYTHON_INTERPRETER,
        "python_interpreter_source": PYTHON_INTERPRETER_SOURCE,
    }
    if details:
        error_details.update(details)
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "error": {
                "code": "tool_runner.MISSING_RUNTIME_DEPENDENCY",
                "message": (
                    f"{module_name} is not installed in TOOLRUNNER_PYTHON={PYTHON_INTERPRETER} "
                    f"(source={PYTHON_INTERPRETER_SOURCE})"
                ),
                "details": error_details,
            },
            "result": result,
        },
    )
