import logging
import os
from typing import Any, Dict, List, Optional, Sequence

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

logger = logging.getLogger(__name__)
DEBUG_TOOLS = os.getenv("LLM_DEBUG_TOOLS", "0") == "1"


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

        logger.info(
            "[OpenAI HTTP SEND] chat_completions model=%s messages=%d tools=%d",
            model,
            len(payload["messages"]),
            len(normalized_tools),
        )
        response = await self.client.chat.completions.create(**payload)
        choice = response.choices[0]
        message = choice.message
        tool_calls = collect_chat_completion_tool_calls(message)
        usage_data = chat_usage_to_normalized_usage(response.usage)
        text = message.content or ""
        raw = response.model_dump()
        logger.info(
            "[OpenAI HTTP RCV] chat_completions model=%s response_id=%s text_len=%d tool_calls=%d",
            model,
            raw.get("id"),
            len(text),
            len(tool_calls),
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
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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

        log_tools_count = len(responses_tools) if responses_tools else 0
        logger.info(
            "[OpenAI HTTP SEND] responses model=%s inputs=%d tools=%d",
            model,
            len(input_items),
            log_tools_count,
        )
        response = await self.client.responses.create(**payload)
        raw = response.model_dump()
        text = self._collect_responses_text(raw)
        tool_calls = self._collect_responses_tool_calls(raw)
        usage_data = self._responses_usage_to_chat_usage(raw.get("usage") or {})
        response_id = raw.get("id")
        logger.info(
            "[OpenAI HTTP RCV] responses model=%s response_id=%s text_len=%d tool_calls=%d",
            model,
            response_id,
            len(text),
            len(tool_calls),
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
    def _messages_to_responses_input(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return messages_to_responses_input(messages)

    @staticmethod
    def _responses_usage_to_chat_usage(usage: Dict[str, Any]) -> Dict[str, Optional[int]]:
        return responses_usage_to_chat_usage(usage)

    @staticmethod
    def _collect_responses_text(response: Dict[str, Any]) -> str:
        return collect_openai_compatible_text(response)

    @staticmethod
    def _collect_responses_tool_calls(response: Dict[str, Any]) -> List[Dict[str, str]]:
        return collect_responses_tool_calls(response)
