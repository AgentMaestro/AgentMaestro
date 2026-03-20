import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from django.conf import settings
from openai import AsyncOpenAI

from .common import (
    chat_usage_to_normalized_usage,
    collect_chat_completion_tool_calls,
    collect_openai_compatible_text,
    collect_responses_tool_calls,
    format_openai_compatible_responses_tools,
    messages_to_responses_input,
    normalize_openai_compatible_tools,
    responses_usage_to_chat_usage,
)
from runs.services.input_items import build_input_items

logger = logging.getLogger(__name__)


def _show_condensed_system_logs() -> bool:
    return getattr(settings, "SHOW_CONDENSED_SYSTEM_LOGS", True)


def _json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _tool_names(tools: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        func = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(func.get("name") or tool.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _summarize_response(raw: Dict[str, Any], text: str, tool_calls: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    response_id = str(raw.get("id") or raw.get("response_id") or "").strip()
    if response_id:
        summary["response_id"] = response_id
    summary["text_len"] = len(text or "")
    summary["tool_calls"] = len(tool_calls)
    if raw.get("model"):
        summary["model"] = raw.get("model")
    return summary


class OpenAIHTTPService:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        outstanding_provider_call_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            previous_response_id=previous_response_id,
            outstanding_provider_call_id=outstanding_provider_call_id,
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
        previous_response_id: Optional[str] = None,
        outstanding_provider_call_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
        }
        raw_tools = tools or []
        normalized_tools = self._normalize_tools(raw_tools)
        if normalized_tools:
            payload["tools"] = normalized_tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_completion_tokens"] = max_output_tokens
        if extra:
            payload.update(extra)
        if _show_condensed_system_logs():
            log_payload = {
                "model": model,
                "messages": len(payload["messages"]),
                "tools": _tool_names(normalized_tools),
            }
        else:
            log_payload = payload
        logger.info(
            "[OpenAI HTTP SEND] chat_completions model=%s payload=%s",
            model,
            _json_dump(log_payload),
        )
        response = await self.client.chat.completions.create(**payload)
        choice = response.choices[0]
        message = choice.message
        tool_calls = collect_chat_completion_tool_calls(message)
        usage_data = chat_usage_to_normalized_usage(response.usage)
        text = message.content or ""
        raw = response.model_dump()
        if _show_condensed_system_logs():
            log_payload = _summarize_response(raw, text, tool_calls)
        else:
            log_payload = raw
        logger.info(
            "[OpenAI HTTP RCV] chat_completions model=%s response=%s",
            model,
            _json_dump(log_payload),
        )
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": usage_data,
            "raw": raw,
        }

    async def _complete_http_responses(
        self,
        messages: Sequence[Dict[str, Any]],
        model: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        previous_response_id: Optional[str] = None,
        outstanding_provider_call_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        input_items = self._messages_to_responses_input(
            messages,
            previous_response_id=previous_response_id,
            outstanding_provider_call_id=outstanding_provider_call_id,
        )
        responses_tools = self.format_tool_definitions_for_responses(tools)

        payload: Dict[str, Any] = {
            "model": model,
            "input": input_items,
            "store": True,
        }
        previous_response_id = str(previous_response_id or "").strip() or None
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if responses_tools:
            payload["tools"] = responses_tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if extra:
            payload.update(extra)

        if _show_condensed_system_logs():
            log_payload = {
                "model": model,
                "inputs": len(input_items),
                "tools": _tool_names(responses_tools),
                "previous_response_id": previous_response_id,
            }
        else:
            log_payload = payload
        logger.info(
            "[OpenAI HTTP SEND] responses model=%s payload=%s",
            model,
            _json_dump(log_payload),
        )
        response = await self.client.responses.create(**payload)
        raw = response.model_dump()
        text = self._collect_responses_text(raw)
        tool_calls = self._collect_responses_tool_calls(raw)
        usage_data = self._responses_usage_to_chat_usage(raw.get("usage") or {})
        response_id = raw.get("id")
        if _show_condensed_system_logs():
            log_payload = _summarize_response(raw, text, tool_calls)
        else:
            log_payload = raw
        logger.info(
            "[OpenAI HTTP RCV] responses model=%s response=%s",
            model,
            _json_dump(log_payload),
        )
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": usage_data,
            "raw": raw,
            "response_id": response_id,
        }

    @staticmethod
    def _normalize_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        return normalize_openai_compatible_tools(tools or [])

    @staticmethod
    def format_tool_definitions_for_responses(
        tools: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        return format_openai_compatible_responses_tools(tools)

    @staticmethod
    def _messages_to_responses_input(
        messages: Sequence[Dict[str, Any]],
        *,
        previous_response_id: Optional[str] = None,
        outstanding_provider_call_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        previous_response_id = str(previous_response_id or "").strip() or None
        outstanding_provider_call_id = str(outstanding_provider_call_id or "").strip() or None
        if previous_response_id:
            if outstanding_provider_call_id:
                tool_messages: List[Dict[str, Any]] = []
                for message in reversed(messages):
                    if message.get("role") != "tool":
                        if tool_messages:
                            break
                        continue
                    tool_messages.append(message)
                if tool_messages:
                    tool_messages.reverse()
                    return build_input_items(
                        tool_messages,
                        previous_response_id=previous_response_id,
                        outstanding_provider_call_id=outstanding_provider_call_id,
                    )

            for message in reversed(messages):
                if message.get("role") != "user":
                    continue
                return messages_to_responses_input([message])
            return []
        return build_input_items(list(messages))

    @staticmethod
    def _responses_usage_to_chat_usage(usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
        return responses_usage_to_chat_usage(usage)

    @staticmethod
    def _collect_responses_text(response: Dict[str, Any]) -> str:
        return collect_openai_compatible_text(response)

    @staticmethod
    def _collect_responses_tool_calls(response: Dict[str, Any]) -> List[Dict[str, str]]:
        return collect_responses_tool_calls(response)
