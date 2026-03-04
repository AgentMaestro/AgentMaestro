import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from openai import AsyncOpenAI
from openai._exceptions import APIStatusError, APITimeoutError, RateLimitError

from .base import BaseLLMClient
from .openai_ws import (
    OpenAIResponsesWSClient,
    OpenAIResponsesWSSessionPool,
    OpenAIResponsesWSPreviousResponseNotFound,
)

logger = logging.getLogger(__name__)
DEBUG_TOOLS = os.getenv("LLM_DEBUG_TOOLS", "0") == "1"
DEFAULT_TRANSPORT = os.getenv("OPENAI_TRANSPORT", "http").lower()


class OpenAIClient(BaseLLMClient):
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        base_url = os.getenv("OPENAI_BASE_URL")
        self.api_key = api_key
        self.base_url = base_url
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.transport = DEFAULT_TRANSPORT
        self.ws_timeout = float(os.getenv("OPENAI_WS_TIMEOUT_SECONDS", "120"))
        self.ws_idle_timeout = float(os.getenv("OPENAI_WS_IDLE_TIMEOUT_SECONDS", "60"))
        self.ws_session_pool = OpenAIResponsesWSSessionPool(
            api_key=api_key,
            base_url=base_url,
            idle_timeout_seconds=self.ws_idle_timeout,
            timeout_seconds=self.ws_timeout,
        )

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        transport = os.getenv("OPENAI_TRANSPORT", self.transport).lower()
        if transport == "ws":
            return await self._complete_ws(messages, model, tools=tools)

        # HTTP transport: prefer the Responses API (v1/responses) so both transports
        # normalize to the same tool-call shape.
        http_mode = os.getenv("OPENAI_HTTP_MODE", "responses").lower()
        if http_mode == "chat_completions":
            return await self._complete_chat_completions(
                messages,
                model,
                tools=tools,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                extra=extra,
            )

        return await self._complete_http_responses(
            messages,
            model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            extra=extra,
        )

    async def _complete_chat_completions(
        self,
        messages: Sequence[Dict[str, Any]],
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Legacy HTTP path (kept as an escape hatch)."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }
        raw_tools = tools or []
        if DEBUG_TOOLS and raw_tools:
            first_raw = raw_tools[0]
            logger.debug("LLM raw tool payload: %r", first_raw)
            logger.debug("LLM raw tool name type: %s", type(first_raw.get("name")))
            logger.debug(
                "LLM raw tool function name type: %s",
                type(first_raw.get("function", {}).get("name")),
            )
        normalized_tools = self._normalize_tools(raw_tools)
        if normalized_tools:
            payload["tools"] = normalized_tools
            payload["tool_choice"] = "auto"
            if DEBUG_TOOLS:
                first = normalized_tools[0]
                logger.debug("LLM tool payload:%s", first)
                logger.debug(
                    "LLM tool name type:%s", type(first.get("function", {}).get("name"))
                )
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_completion_tokens"] = max_output_tokens
        if extra:
            payload.update(extra)

        response = await self.client.chat.completions.create(**payload)
        choice = response.choices[0]
        message = choice.message
        tool_calls = []
        if message.tool_calls:
            for call in message.tool_calls:
                fn = call.function
                tool_calls.append(
                    {
                        "id": call.id,
                        "name": fn.name,
                        "arguments": fn.arguments or "{}",
                    }
                )
        usage = response.usage
        usage_data = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        return {
            "text": message.content or "",
            "tool_calls": tool_calls,
            "usage": usage_data,
            "raw": response.model_dump(),
        }

    async def _complete_http_responses(
        self,
        messages: Sequence[Dict[str, Any]],
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """HTTP transport using the OpenAI Responses API."""
        input_items = self._messages_to_responses_input(messages)
        responses_tools = self.format_tool_definitions_for_responses(tools)

        payload: Dict[str, Any] = {
            "model": model,
            "input": input_items,
            "store": False,
        }
        if responses_tools:
            payload["tools"] = responses_tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if extra:
            payload.update(extra)

        # openai-python exposes `client.responses.create(...)`.
        response = await self.client.responses.create(**payload)
        raw = response.model_dump()
        usage_data = self._responses_usage_to_chat_usage(raw.get("usage") or {})
        return {
            "text": self._collect_responses_text(raw),
            "tool_calls": self._collect_responses_tool_calls(raw),
            "usage": usage_data,
            "raw": raw,
            "response_id": raw.get("id"),
        }

    async def _complete_ws(
        self,
        messages: Sequence[Dict[str, Any]],
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        system_text, user_text = self._messages_to_input(messages)
        ws_client = OpenAIResponsesWSClient(self.api_key, base_url=self.base_url, timeout=self.ws_timeout)
        response = await ws_client.create_response(model=model, input_text=user_text, system_text=system_text)
        return {
            "text": response["text"],
            "tool_calls": response.get("tool_calls", []),
            "usage": {},
            "raw": response["raw"],
            "response_id": response.get("response_id"),
        }

    def _messages_to_responses_input(self, messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert chat-style history into Responses API input items."""
        items: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("id") or "").strip()
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
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        return items

    def _responses_usage_to_chat_usage(self, usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """Map Responses API usage keys to our normalized chat usage keys."""
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

    def _collect_responses_text(self, response: Dict[str, Any]) -> str:
        """Best-effort extraction of assistant text from Responses API payload."""
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

    def _collect_responses_tool_calls(self, response: Dict[str, Any]) -> List[Dict[str, str]]:
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

    def _messages_to_input(self, messages: Sequence[Dict[str, Any]]) -> tuple[Optional[str], str]:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for msg in messages:
            role = msg.get("role")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                user_parts.append(content)
        system_text = " ".join(system_parts).strip() if system_parts else None
        user_text = " ".join(user_parts).strip()
        return system_text, user_text

    async def stream_text(self, *args: Any, **kwargs: Any):
        # Streaming can be added later; current runner uses complete.
        raise NotImplementedError("OpenAI streaming not implemented yet")

    def _normalize_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise ValueError(f"Invalid tool definition: {tool!r}")
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                func = tool["function"]
                name = func.get("name")
                if not isinstance(name, str):
                    raise ValueError(f"Function name must be string, got {type(name).__name__}")
                description = func.get("description", "")
                params = self._normalize_parameters(func.get("parameters"))
            else:
                name = tool.get("name")
                if not isinstance(name, str):
                    raise ValueError(f"Tool name must be string, got {type(name).__name__}")
                description = tool.get("description", "")
                params = self._normalize_parameters(tool.get("parameters"))
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

    def format_tool_definitions_for_responses(
        self, tools: Optional[List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        raw_tools = tools or []
        normalized = self._normalize_tools(raw_tools)
        formatted: List[Dict[str, Any]] = []
        for tool in normalized:
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

    async def get_ws_session(self, run_id: str, model: str):
        return await self.ws_session_pool.get(run_id, model)

    async def close_ws_session(self, run_id: str) -> None:
        await self.ws_session_pool.close(run_id)

    async def cleanup_ws_sessions(self) -> None:
        await self.ws_session_pool.cleanup()

    def _normalize_parameters(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(params, dict) or not params:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        schema = dict(params)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("additionalProperties", True)
        return schema


def is_transient_error(exc: Exception) -> bool:
    return isinstance(exc, (RateLimitError, APITimeoutError, APIStatusError)) and getattr(
        exc, "status_code", 500
    ) in {408, 409, 429, 500, 502, 503, 504}


async def retry_with_backoff(func, *, max_retries: int = 3, base_delay: float = 1.0):
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as exc:
            if attempt >= max_retries or not is_transient_error(exc):
                raise
            delay = base_delay * (2**attempt)
            await asyncio.sleep(delay)