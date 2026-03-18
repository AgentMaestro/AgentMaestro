import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .common import normalize_openai_compatible_tools

logger = logging.getLogger(__name__)
DEFAULT_CLIENT_NAME = "agentmaestro/1.0"
DEFAULT_TIMEOUT_SECONDS = 60.0


class GeminiHTTPService:
    def __init__(
            self,
            base_url: str,
            api_key: str,
            *,
            client_name: str | None = None,
            timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client_name = client_name or DEFAULT_CLIENT_NAME
        self.timeout = timeout_seconds

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
        contents, system_instruction = self._build_contents(messages)
        normalized_tools = normalize_openai_compatible_tools(tools or [])
        payload: Dict[str, Any] = {"model": model, "contents": contents}
        generation_config = self._build_generation_config(
            normalized_tools, temperature, max_output_tokens
        )
        if generation_config:
            payload["generationConfig"] = generation_config
        tool_payload = self._build_tool_payload(normalized_tools)
        if tool_payload:
            payload.update(tool_payload)
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if extra:
            payload.update(extra)

        logger.info(
            "[Gemini HTTP SEND] model=%s contents=%d tools=%d",
            model,
            len(contents),
            len(normalized_tools),
        )
        url = f"{self.base_url}/{self._format_model_path(model)}:generateContent"
        headers = self._build_headers()
        tool_names = [
            entry.get("function", {}).get("name")
            for entry in normalized_tools
            if isinstance(entry, dict)
            and entry.get("function", {}).get("name")
        ]
        payload_summary = {
            "content_count": len(contents),
            "tool_count": len(tool_names),
            "tool_names": tool_names[:20],
            "has_system_instruction": bool(system_instruction),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                response_text = exc.response.text or ""
                truncated_response = (
                    response_text
                    if len(response_text) <= 2000
                    else response_text[:2000] + "..."
                )
                summary_text = json.dumps(payload_summary, ensure_ascii=False)
                logger.error(
                    "[Gemini HTTP ERROR] status=%s url=%s payload_summary=%s response=%s",
                    exc.response.status_code,
                    url,
                    summary_text,
                    truncated_response,
                )
                exc.args = (
                    f"Gemini HTTP error {exc.response.status_code}: {truncated_response}",
                )
                raise
            data = response.json()

        candidate = self._extract_candidate(data)
        text = self._collect_candidate_text(candidate)
        tool_calls = self._collect_tool_calls(candidate, data)
        usage = self._collect_usage(data.get("usageMetadata") or {})
        response_id = data.get("name") or data.get("responseId") or ""
        logger.info(
            "[Gemini HTTP RCV] model=%s response_id=%s text_len=%d tool_calls=%d",
            model,
            response_id,
            len(text),
            len(tool_calls),
        )
        return {
            "text": text,
            "tool_calls": tool_calls,
            "usage": usage,
            "raw": data,
            "response_id": response_id,
        }

    def _build_contents(
            self, messages: Sequence[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], str | None]:
        contents: List[Dict[str, Any]] = []
        system_texts: List[str] = []
        for msg in messages:
            role = (msg.get("role") or "").strip()
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                system_texts.append(content)
                continue
            normalized_role = "model" if role in {"assistant", "tool"} else "user"
            contents.append({"role": normalized_role, "parts": [{"text": content}]})
        if not contents:
            contents.append({"role": "user", "parts": [{"text": ""}]})
        instruction = "\n".join(system_texts).strip() or None
        return contents, instruction

    def _build_config(
            self,
            normalized_tools: List[Dict[str, Any]],
            temperature: Optional[float],
            max_output_tokens: Optional[int],
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {}
        tool_config = self._build_tool_config(normalized_tools)
        if tool_config:
            config.update(tool_config)
        if temperature is not None:
            config["temperature"] = temperature
        if max_output_tokens is not None:
            config["maxOutputTokens"] = max_output_tokens
        return config

    def _build_tool_config(self, normalized_tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        function_declarations: List[Dict[str, Any]] = []
        for entry in normalized_tools:
            function = entry.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            params = self._clean_tool_parameters(function.get("parameters")) or {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
            function_declarations.append(
                {
                    "name": name,
                    "description": function.get("description") or "",
                    "parameters": params,
                }
            )
        if not function_declarations:
            return {}
        return {
            "tools": [
                {
                    "functionDeclarations": function_declarations,
                }
            ]
        }

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
            "x-goog-api-client": self.client_name,
            "User-Agent": self.client_name,
        }

    @staticmethod
    def _format_model_path(model: str) -> str:
        normalized = (model or "").strip()
        if not normalized:
            raise ValueError("Model name is required for Gemini requests")
        if normalized.startswith("models/"):
            return normalized
        return f"models/{normalized}"

    @staticmethod
    def _extract_candidate(data: Dict[str, Any]) -> Dict[str, Any]:
        candidates = data.get("candidates") or []
        if isinstance(candidates, dict):
            candidates = [candidates]
        if candidates:
            return candidates[0] or {}
        return data

    @staticmethod
    def _collect_candidate_text(candidate: Dict[str, Any]) -> str:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        chunks: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
        return "\n".join(chunks).strip()

    def _collect_tool_calls(
            self, candidate: Dict[str, Any], response_data: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        calls: List[Dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for call in self._iter_function_calls(candidate):
            self._add_tool_call(calls, seen, call)
        for call in self._iter_function_calls(response_data):
            self._add_tool_call(calls, seen, call)
        content_parts = (candidate.get("content") or {}).get("parts") or []
        for part in content_parts:
            if not isinstance(part, dict):
                continue
            part_call = part.get("function_call") or part.get("functionCall")
            self._add_tool_call(calls, seen, part_call)
        return calls

    def _build_generation_config(
            self,
            normalized_tools: List[Dict[str, Any]],
            temperature: Optional[float],
            max_output_tokens: Optional[int],
    ) -> Dict[str, Any]:
        generation_config: Dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        return generation_config

    @staticmethod
    def _clean_tool_parameters(params: Any, parent_type: str | None = None) -> Any:
        if not isinstance(params, dict):
            return params
        cleaned: Dict[str, Any] = {}
        unsupported = {"examples", "additionalProperties"}
        declared_type = params.get("type")
        type_hint = declared_type or parent_type
        for key, value in params.items():
            if key in unsupported:
                continue
            if isinstance(value, dict):
                cleaned[key] = GeminiHTTPService._clean_tool_parameters(
                    value, declared_type or parent_type
                )
            elif isinstance(value, list):
                cleaned[key] = [
                    GeminiHTTPService._clean_tool_parameters(item, declared_type or parent_type)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                cleaned[key] = value
        enum_values = cleaned.get("enum")
        if isinstance(enum_values, list):
            if isinstance(type_hint, str) and type_hint.lower() == "string":
                cleaned["enum"] = [str(item) if not isinstance(item, str) else item for item in enum_values]
            else:
                cleaned.pop("enum", None)
        return cleaned

    def _build_tool_payload(self, normalized_tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not normalized_tools:
            return {}
        function_declarations: List[Dict[str, Any]] = []
        for entry in normalized_tools:
            function = entry.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            params = self._clean_tool_parameters(function.get("parameters")) or {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
            function_declarations.append(
                {
                    "name": name,
                    "description": function.get("description") or "",
                    "parameters": params,
                }
            )
        payload: Dict[str, Any] = {
            "tools": [
                {
                    "functionDeclarations": function_declarations,
                }
            ]
        }
        return payload

    @staticmethod
    def _iter_function_calls(source: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        if not isinstance(source, dict):
            return ()
        for key in ("functionCalls", "function_calls", "functionCall", "function_call"):
            value = source.get(key)
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        yield entry
            elif isinstance(value, dict):
                yield value

    def _add_tool_call(
            self, calls: List[Dict[str, str]], seen: set[tuple[str, str, str]], call: Any
    ) -> None:
        if not isinstance(call, dict):
            return
        name = call.get("name")
        if not name:
            return
        call_id = str(call.get("callId") or call.get("id") or f"{name}-{len(calls)}")
        arguments = self._serialize_arguments(call.get("args") or call.get("arguments"))
        key = (name, call_id, arguments)
        if key in seen:
            return
        seen.add(key)
        calls.append({"id": call_id, "name": name, "arguments": arguments})

    @staticmethod
    def _serialize_arguments(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)

    @staticmethod
    def _collect_usage(metadata: Dict[str, Any]) -> Dict[str, Optional[int]]:
        def _maybe_int(val: Any) -> Optional[int]:
            if isinstance(val, (int, float)):
                return int(val)
            return None

        return {
            "prompt_tokens": _maybe_int(
                metadata.get("promptTokenCount") or metadata.get("prompt_token_count")
            ),
            "completion_tokens": _maybe_int(
                metadata.get("candidatesTokenCount")
                or metadata.get("candidates_token_count")
                or metadata.get("outputTokenCount")
                or metadata.get("output_token_count")
            ),
            "total_tokens": _maybe_int(
                metadata.get("totalTokenCount") or metadata.get("total_token_count")
            ),
        }