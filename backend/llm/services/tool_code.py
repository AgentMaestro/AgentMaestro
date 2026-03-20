from __future__ import annotations

import ast
import json
import re
import uuid
from typing import Any, Iterable

DEFAULT_API_CALL_RE = re.compile(r"default_api\.(?P<name>[A-Za-z_]\w*)\(")


def _normalize_allowed_tool_names(allowed_tool_names: Iterable[str]) -> set[str]:
    return {str(name or "").strip() for name in allowed_tool_names if str(name or "").strip()}


def _extract_balanced_call(text: str, start_index: int) -> tuple[str | None, int | None]:
    open_index = text.find("(", start_index)
    if open_index < 0:
        return None, None

    depth = 0
    quote_char: str | None = None
    escape = False
    for index in range(open_index, len(text)):
        char = text[index]
        if quote_char is not None:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote_char:
                quote_char = None
            continue
        if char in {"'", '"'}:
            quote_char = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1], index + 1
    return None, None


def _parse_default_api_call(call_text: str, allowed_tool_names: set[str]) -> dict[str, Any] | None:
    candidate = call_text.strip()
    if candidate.startswith("print(") and candidate.endswith(")"):
        candidate = candidate[len("print("):-1].strip()
    if not candidate.startswith("default_api."):
        return None

    try:
        parsed = ast.parse(candidate, mode="eval")
    except SyntaxError:
        return None

    expr = parsed.body
    if not isinstance(expr, ast.Call) or expr.args:
        return None
    func = expr.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "default_api"
    ):
        return None

    tool_name = str(func.attr or "").strip()
    if not tool_name or tool_name not in allowed_tool_names:
        return None

    arguments: dict[str, Any] = {}
    for keyword in expr.keywords:
        if keyword.arg is None:
            return None
        try:
            arguments[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError):
            return None

    call_id = str(uuid.uuid4())
    return {
        "id": call_id,
        "call_id": call_id,
        "name": tool_name,
        "arguments": arguments,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def is_code_like_tool_output(text: str | None) -> bool:
    value = str(text or "")
    return "tool_code" in value or "default_api." in value


def extract_code_like_tool_calls(
    text: str | None,
    allowed_tool_names: Iterable[str],
) -> list[dict[str, Any]]:
    value = str(text or "")
    if not value or "default_api." not in value:
        return []

    allowed = _normalize_allowed_tool_names(allowed_tool_names)
    if not allowed:
        return []

    tool_calls: list[dict[str, Any]] = []
    search_index = 0
    while True:
        match = DEFAULT_API_CALL_RE.search(value, search_index)
        if not match:
            break
        call_text, end_index = _extract_balanced_call(value, match.start())
        if call_text:
            parsed = _parse_default_api_call(call_text, allowed)
            if parsed:
                tool_calls.append(parsed)
        search_index = end_index or match.end()
    return tool_calls


def build_code_like_tool_repair_message(allowed_tool_names: Iterable[str]) -> str:
    allowed = sorted(_normalize_allowed_tool_names(allowed_tool_names))
    allowed_text = ", ".join(allowed) if allowed else "the available tools"
    return (
        "The previous assistant turn used code-like tool stand-in text instead of the real tool protocol. "
        "Invoke the real tool directly. Do not emit `tool_code`, `print(default_api.<tool>(...))`, "
        "or similar wrappers as if they were executed tools. "
        f"If a memory action is needed, use {allowed_text} directly."
    )
