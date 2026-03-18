import json
from typing import Any, Dict, List, Optional, Sequence


def normalize_openai_compatible_parameters(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(params, dict) or not params:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    schema = dict(params)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("additionalProperties", True)
    return schema


def normalize_openai_compatible_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            raise ValueError(f"Invalid tool definition: {tool!r}")
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            func = tool["function"]
            name = func.get("name")
            if not isinstance(name, str):
                raise ValueError(f"Function name must be string, got {type(name).__name__}")
            description = func.get("description", "")
            params = normalize_openai_compatible_parameters(func.get("parameters"))
        else:
            name = tool.get("name")
            if not isinstance(name, str):
                raise ValueError(f"Tool name must be string, got {type(name).__name__}")
            description = tool.get("description", "")
            params = normalize_openai_compatible_parameters(tool.get("parameters"))
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            }
        )
    return normalized


def format_openai_compatible_responses_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for tool in normalize_openai_compatible_tools(tools):
        func = tool["function"]
        formatted.append(
            {
                "type": "function",
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": func.get("parameters") or {"type": "object", "properties": {}, "additionalProperties": True},
            }
        )
    return formatted


def messages_to_responses_input(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            call_id = str(msg.get("provider_call_id") or msg.get("tool_call_id") or msg.get("id") or "").strip()
            content = msg.get("content")
            if call_id and content is not None:
                if isinstance(content, (dict, list)):
                    output = json.dumps(content, ensure_ascii=False)
                else:
                    output = str(content)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    }
                )
            continue

        if role not in {"system", "user", "assistant"}:
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            content_item = {"type": "output_text", "text": content}
        else:
            content_item = {"type": "input_text", "text": content}
        items.append(
            {
                "type": "message",
                "role": role,
                "content": [content_item],
            }
        )
    return items


def responses_usage_to_chat_usage(usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    prompt = usage.get("input_tokens")
    completion = usage.get("output_tokens")
    total = usage.get("total_tokens")
    return {
        "prompt_tokens": int(prompt) if isinstance(prompt, (int, float)) else None,
        "completion_tokens": int(completion) if isinstance(completion, (int, float)) else None,
        "total_tokens": int(total) if isinstance(total, (int, float)) else None,
    }


def chat_usage_to_normalized_usage(usage: Any) -> Dict[str, Optional[int]]:
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def collect_openai_compatible_text(response: Dict[str, Any]) -> str:
    chunks: List[str] = []

    def add_text(value: Any) -> None:
        if isinstance(value, str):
            if value.strip():
                chunks.append(value)
            return
        if isinstance(value, dict):
            text_value = value.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value)
            for key in ("content", "items", "output", "output_text"):
                if key in value:
                    add_text(value[key])
            return
        if isinstance(value, list):
            for item in value:
                add_text(item)

    add_text(response.get("output_text") or [])
    add_text(response.get("output") or [])
    return "".join(chunks).strip()


def collect_responses_tool_calls(response: Dict[str, Any]) -> List[Dict[str, str]]:
    outputs = response.get("output") or []
    calls: List[Dict[str, str]] = []
    if not isinstance(outputs, list):
        return calls
    for item in outputs:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in {"function_call", "tool_call", "custom_tool_call"}:
            continue
        payload = item.get("function") if isinstance(item.get("function"), dict) else item
        call_id = (
            str(payload.get("call_id") or "")
            or str(payload.get("id") or "")
            or str(payload.get("tool_call_id") or "")
        )
        if not call_id:
            continue
        name = payload.get("name") or item.get("name") or ""
        arguments = (
            payload.get("arguments")
            or payload.get("input")
            or item.get("arguments")
            or item.get("input")
            or ""
        )
        if isinstance(arguments, (dict, list)):
            arguments_str = json.dumps(arguments, ensure_ascii=False)
        else:
            arguments_str = "" if arguments is None else str(arguments)
        calls.append({"id": call_id, "name": str(name), "arguments": arguments_str})
    return calls


def collect_chat_completion_tool_calls(message: Any) -> List[Dict[str, str]]:
    tool_calls: List[Dict[str, str]] = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        tool_calls.append(
            {
                "id": str(getattr(call, "id", "") or ""),
                "name": str(getattr(fn, "name", "") or ""),
                "arguments": str(getattr(fn, "arguments", "") or "{}"),
            }
        )
    return tool_calls


def messages_to_system_and_user_input(messages: Sequence[Dict[str, Any]]) -> tuple[str | None, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)
    system_text = " ".join(system_parts).strip() if system_parts else None
    user_text = " ".join(user_parts).strip()
    return system_text, user_text
