import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from llm.models import AgentRole, LLMMessage, LLMModelProfile, LLMRun, LLMToolCall, MessageRole, RunStatus
from llm.services.model_failover import (
    build_model_failover_candidates,
    is_retryable_model_failure,
    normalize_backup_retry_policy,
)
from llm.services.providers.retry import retry_with_backoff
from llm.services.registry import get_client
from llm.services.tool_code import extract_code_like_tool_calls
from llm.services.tool_schemas import get_tool_schemas
from llm.services.toolrunner_bridge import run_tool
from runs.services.input_items import build_ws_request_input_items

logger = logging.getLogger(__name__)
HEADLESS_WS_INIT_LOG_PREFIX = "[HEADLESS-WS-INIT]"
HEADLESS_WS_DISPATCH_LOG_PREFIX = "[HEADLESS-WS-DISPATCH]"


def _call_id_value(call_payload):
    return (
        str(call_payload.get("call_id") or "")
        or str(call_payload.get("id") or "")
        or str(call_payload.get("tool_call_id") or "")
    )


class LLMRunTimeLimitExceeded(RuntimeError):
    pass


class LLMRunner:
    def __init__(self):
        self.default_provider = getattr(settings, "LLM_PROVIDER", "openai")
        self.default_planner = getattr(settings, "LLM_DEFAULT_PROFILE_PLANNER", "Maestro")
        self.default_coder = getattr(settings, "LLM_DEFAULT_PROFILE_CODER", "Apprentice")
        self.max_retries = getattr(settings, "LLM_MAX_RETRIES", 3)
        self.timeout_seconds = getattr(settings, "LLM_TIMEOUT_SECONDS", 60)

    async def run(
        self,
        *,
        prompt: str,
        agent_name: Optional[str] = None,
        agent_role: Optional[str] = None,
        profile_name: Optional[str] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        backup_models: Optional[List[Dict[str, Any]]] = None,
        backup_retry_policy: Optional[Dict[str, Any]] = None,
        orchestration_run_id: Optional[str] = None,
        purpose: Optional[str] = None,
        max_tool_rounds: int = 3,
        max_elapsed_seconds: Optional[int] = None,
        messages: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        explicit_config = provider is not None and model_name is not None
        if explicit_config:
            profile = None
            provider = str(provider or self.default_provider)
            model_name = str(model_name or "")
        else:
            profile = await self._resolve_profile(profile_name, agent_role)
            provider = profile.provider if profile else self.default_provider
            model_name = profile.model if profile else ""
            temperature = profile.temperature if profile else temperature
            max_output_tokens = profile.max_output_tokens if profile else max_output_tokens
            extra = profile.extra if profile else extra
        agent_display = agent_name or (profile.name if profile else "Unnamed")
        model_candidates = await sync_to_async(build_model_failover_candidates)(
            primary_provider=provider,
            primary_model=model_name,
            backup_models=backup_models,
            default_provider=provider,
        )
        if not model_candidates:
            model_candidates = [{"provider": provider, "model": model_name, "source": "primary"}]
        backup_retry_policy = normalize_backup_retry_policy(backup_retry_policy)

        run = await sync_to_async(LLMRun.objects.create)(
            provider=provider,
            model=model_name,
            profile=profile,
            orchestration_run_id=orchestration_run_id,
            agent_name=agent_display,
            purpose=purpose or prompt[:200],
            status=RunStatus.STARTED,
        )

        history: List[Dict[str, Any]] = list(messages or [])
        if prompt:
            history.append({"role": "user", "content": prompt})
            await self._persist_message(run, MessageRole.USER, prompt)

        tool_call_count = 0
        error_message: Optional[str] = None
        error_type: Optional[str] = None
        error_classification: Optional[str] = None
        usage_totals = {"token_prompt": 0, "token_completion": 0, "token_total": 0}

        async def _execute_transport() -> Dict[str, Any]:
            nonlocal tool_call_count, usage_totals, provider, model_name

            resolved_tools = tools if tools is not None else get_tool_schemas()
            allowed_tool_names = {
                str(tool.get("name") or "").strip()
                for tool in resolved_tools
                if isinstance(tool, dict) and str(tool.get("name") or "").strip()
            }
            tool_rounds = 0
            candidate_index = 0
            while candidate_index < len(model_candidates):
                candidate = model_candidates[candidate_index]
                moved_to_next_candidate = False
                provider = candidate["provider"]
                model_name = candidate["model"]
                await self._update_run_provider_model(run, provider, model_name)
                client = get_client(provider)
                transport = self._resolve_client_transport(client)

                if transport == "ws":
                    try:
                        return await self._run_ws_transport(
                            client=client,
                            run=run,
                            history=history,
                            tools=resolved_tools,
                            model_name=model_name,
                            orchestration_run_id=orchestration_run_id,
                            max_tool_rounds=max_tool_rounds,
                        )
                    except Exception as exc:
                        if (
                            candidate_index + 1 < len(model_candidates)
                            and is_retryable_model_failure(
                                exc,
                                client=client,
                                retry_policy=backup_retry_policy,
                            )
                        ):
                            close_session = getattr(client, "close_ws_session", None)
                            if callable(close_session):
                                try:
                                    await close_session(str(run.id), model=model_name)
                                except Exception:
                                    logger.exception(
                                        "Failed to close ws session before model failover run=%s provider=%s model=%s",
                                        run.id,
                                        provider,
                                        model_name,
                                    )
                            logger.warning(
                                "LLMRunner failover from provider=%s model=%s to provider=%s model=%s run=%s error=%s",
                                provider,
                                model_name,
                                model_candidates[candidate_index + 1]["provider"],
                                model_candidates[candidate_index + 1]["model"],
                                run.id,
                                exc,
                            )
                            candidate_index += 1
                            moved_to_next_candidate = True
                            break
                        raise

                while True:
                    response = None
                    max_same_model_retries = int(backup_retry_policy.get("retry_same_model_attempts", 1) or 0)
                    transient_checker = getattr(client, "is_transient_error", None)
                    try:
                        for attempt in range(max_same_model_retries + 1):
                            try:
                                response = await client.complete(
                                    history,
                                    model=model_name,
                                    tools=resolved_tools,
                                    temperature=temperature,
                                    max_output_tokens=max_output_tokens,
                                    extra=extra,
                                )
                                break
                            except Exception as exc:
                                if attempt >= max_same_model_retries or not (
                                    callable(transient_checker) and transient_checker(exc)
                                ):
                                    raise
                                await asyncio.sleep(float(2**attempt))
                    except Exception as exc:
                        if candidate_index + 1 < len(model_candidates) and is_retryable_model_failure(
                            exc,
                            client=client,
                            retry_policy=backup_retry_policy,
                        ):
                            logger.warning(
                                "LLMRunner model failover run=%s provider=%s model=%s next_provider=%s next_model=%s error=%s",
                                run.id,
                                provider,
                                model_name,
                                model_candidates[candidate_index + 1]["provider"],
                                model_candidates[candidate_index + 1]["model"],
                                exc,
                            )
                            candidate_index += 1
                            moved_to_next_candidate = True
                            break
                        raise

                    if response is None:
                        raise RuntimeError("LLM completion returned no response")

                    assistant_text = response.get("text") or ""
                    tool_calls = response.get("tool_calls") or []
                    if not tool_calls and assistant_text:
                        synthesized_tool_calls = extract_code_like_tool_calls(assistant_text, allowed_tool_names)
                        if synthesized_tool_calls:
                            logger.warning(
                                "Repaired code-like tool output run=%s provider=%s model=%s tool_names=%s",
                                run.id,
                                provider,
                                model_name,
                                [call.get("name") for call in synthesized_tool_calls],
                            )
                            tool_calls = synthesized_tool_calls
                            assistant_text = ""
                    usage = response.get("usage") or {}

                    await self._persist_message(
                        run,
                        MessageRole.ASSISTANT,
                        assistant_text,
                        meta={
                            "raw": response.get("raw"),
                            "tool_code_repaired": bool(tool_calls and not assistant_text),
                        },
                    )
                    assistant_entry = {"role": "assistant", "content": assistant_text or ""}
                    if tool_calls:
                        converted_tool_calls = []
                        for call in tool_calls:
                            args_raw = call.get("arguments") or {}
                            if isinstance(args_raw, str):
                                arguments = args_raw
                            else:
                                arguments = json.dumps(args_raw, ensure_ascii=False)
                            call_id = _call_id_value(call)
                            converted_tool_calls.append(
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": call.get("name"),
                                        "arguments": arguments,
                                    },
                                }
                            )
                        assistant_entry["tool_calls"] = converted_tool_calls
                    history.append(assistant_entry)

                    normalized_usage = self._normalize_usage(usage)
                    for key in usage_totals:
                        usage_totals[key] += normalized_usage.get(key, 0)
                    await self._update_usage(run, usage_totals)

                    if tool_calls and resolved_tools:
                        for call in tool_calls:
                            tool_name = (call.get("name") or "").strip()
                            args_raw = call.get("arguments") or "{}"
                            args_json: dict[str, Any] = {}
                            parse_error: str | None = None
                            if isinstance(args_raw, str):
                                try:
                                    args_json = json.loads(args_raw)
                                except json.JSONDecodeError:
                                    parse_error = "invalid_tool_call_arguments"
                            elif isinstance(args_raw, dict):
                                args_json = args_raw
                            else:
                                parse_error = "invalid_tool_call_arguments"

                            if not tool_name:
                                parse_error = "invalid_tool_call_missing_name"

                            tool_call_obj = await sync_to_async(LLMToolCall.objects.create)(
                                run=run,
                                tool_name=tool_name or "unknown_tool",
                                arguments=args_json,
                            )
                            if parse_error:
                                result_payload = {"ok": False, "error": parse_error}
                                success = False
                                error_txt = parse_error
                            else:
                                tool_orch_id = orchestration_run_id or str(run.id)
                                result_payload = await self._execute_tool(tool_name, args_json, tool_orch_id)
                                success = result_payload.get("ok", False)
                                error_txt = result_payload.get("error") or ""

                            await sync_to_async(self._finalize_tool_call)(
                                tool_call_obj,
                                success=success,
                                result=result_payload.get("result"),
                                error=error_txt,
                            )

                            tool_message_content = json.dumps(result_payload, ensure_ascii=False)
                            await self._persist_message(
                                run,
                                MessageRole.TOOL,
                                tool_message_content or "",
                                name=tool_name,
                                meta={"tool_call_id": tool_call_obj.id},
                            )
                            history.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.get("id"),
                                    "content": tool_message_content or "",
                                }
                            )
                            tool_call_count += 1

                        tool_rounds += 1
                        if tool_rounds > max_tool_rounds:
                            await self._finalize_run(
                                run,
                                RunStatus.FAILED,
                                error="max_tool_rounds_exceeded",
                                usage=usage_totals,
                                error_meta={"error_type": "ToolRoundLimit", "error": "max_tool_rounds_exceeded"},
                            )
                            return {
                                "run_id": str(run.id),
                                "text": "",
                                "tool_calls_executed": tool_call_count,
                                "status": "failed",
                                "error": "max_tool_rounds_exceeded",
                            }
                        continue

                    await self._finalize_run(run, RunStatus.COMPLETED, usage=usage_totals)
                    return {
                        "run_id": str(run.id),
                        "text": assistant_text,
                        "tool_calls_executed": tool_call_count,
                        "status": "completed",
                        "error": None,
                    }

                if moved_to_next_candidate:
                    continue
                candidate_index += 1

            raise RuntimeError("No LLM model candidates available")

        try:
            if max_elapsed_seconds and int(max_elapsed_seconds) > 0:
                try:
                    return await asyncio.wait_for(_execute_transport(), timeout=float(max_elapsed_seconds))
                except asyncio.TimeoutError as exc:
                    raise LLMRunTimeLimitExceeded("headless_run_time_limit_exceeded") from exc
            return await _execute_transport()
        except LLMRunTimeLimitExceeded as exc:
            error_message = str(exc)
            error_type = type(exc).__name__
            error_classification = "headless_timeout"
        except asyncio.TimeoutError as exc:
            error_message = "TimeoutError"
            error_type = type(exc).__name__
            error_classification = "timeout"
        except Exception as exc:
            error_message = str(exc)
            error_type = type(exc).__name__

        error_meta = {
            "error_type": error_type or "Exception",
            "error": error_message or "",
        }
        if error_classification:
            error_meta["classification"] = error_classification

        await self._finalize_run(run, RunStatus.FAILED, error_message, usage=usage_totals, error_meta=error_meta)
        return {
            "run_id": str(run.id),
            "text": "",
            "tool_calls_executed": tool_call_count,
            "status": "failed",
            "error": error_message,
        }

    def _resolve_client_transport(self, client) -> str:
        resolver = getattr(client, "resolve_transport", None)
        if callable(resolver):
            return str(resolver() or "http").lower()
        return str(getattr(client, "transport", "http") or "http").lower()

    async def _resolve_profile(self, profile_name: Optional[str], agent_role: Optional[str]):
        query = LLMModelProfile.objects.filter(is_active=True)
        if profile_name:
            return await sync_to_async(query.filter(name=profile_name).first)()
        if agent_role == AgentRole.PLANNER:
            return await sync_to_async(query.filter(name=self.default_planner).first)()
        if agent_role == AgentRole.CODER:
            return await sync_to_async(query.filter(name=self.default_coder).first)()
        return await sync_to_async(query.first)()

    async def _persist_message(
        self,
        run: LLMRun,
        role: str,
        content: str,
        *,
        name: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        await sync_to_async(LLMMessage.objects.create)(
            run=run,
            role=role,
            content=content or "",
            name=name or "",
            meta=meta or {},
        )

    def _normalize_usage(self, usage: Dict[str, Any]) -> Dict[str, int]:
        return {
            "token_prompt": int(usage.get("prompt_tokens") or 0),
            "token_completion": int(usage.get("completion_tokens") or 0),
            "token_total": int(usage.get("total_tokens") or 0),
        }

    async def _update_usage(self, run: LLMRun, usage: Dict[str, Any]):
        fields = {
            "token_prompt": usage.get("token_prompt"),
            "token_completion": usage.get("token_completion"),
            "token_total": usage.get("token_total"),
        }
        await sync_to_async(LLMRun.objects.filter(id=run.id).update)(**fields)

    async def _update_run_provider_model(self, run: LLMRun, provider: str, model_name: str) -> None:
        provider_value = str(provider or "").strip() or run.provider
        model_value = str(model_name or "").strip() or run.model
        if run.provider == provider_value and run.model == model_value:
            return
        run.provider = provider_value
        run.model = model_value
        await sync_to_async(LLMRun.objects.filter(id=run.id).update)(
            provider=provider_value,
            model=model_value,
        )

    async def _run_ws_transport(
        self,
        *,
        client,
        run: LLMRun,
        history: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_name: str,
        orchestration_run_id: Optional[str],
        max_tool_rounds: int,
    ) -> Dict[str, Any]:
        tool_call_count = 0
        tool_rounds = 0
        usage_totals = {"token_prompt": 0, "token_completion": 0, "token_total": 0}

        await client.cleanup_ws_sessions()
        session = await client.get_ws_session(str(run.id), model_name)
        session_tools = client.format_tool_definitions_for_responses(tools)
        allowed_tool_names = {
            str(tool.get("name") or "").strip()
            for tool in tools
            if isinstance(tool, dict) and str(tool.get("name") or "").strip()
        }

        initial_input_items = self._build_ws_input_items(
            history,
            previous_response_id=getattr(session, "previous_response_id", None),
            run_id=str(run.id),
        )
        initial_payload_snapshot: dict[str, Any] = {
            "type": "response.create",
            "model": model_name,
            "store": True,
            "input": initial_input_items,
            "previous_response_id": getattr(session, "previous_response_id", None),
        }
        if session_tools:
            initial_payload_snapshot["tools"] = session_tools
        logger.info(
            "%s run=%s orchestration_run_id=%s payload=%s",
            HEADLESS_WS_INIT_LOG_PREFIX,
            run.id,
            orchestration_run_id,
            json.dumps(initial_payload_snapshot, ensure_ascii=False)[:12000],
        )

        input_items = initial_input_items
        final_text = ""

        while True:
            send_tools = bool(session_tools) and session.should_send_tools()
            payload_snapshot: dict[str, Any] = {
                "type": "response.create",
                "model": model_name,
                "store": True,
                "input": input_items,
                "previous_response_id": getattr(session, "previous_response_id", None),
            }
            if send_tools and session_tools:
                payload_snapshot["tools"] = session_tools
            logger.info(
                "%s run=%s orchestration_run_id=%s payload=%s",
                HEADLESS_WS_DISPATCH_LOG_PREFIX,
                run.id,
                orchestration_run_id,
                json.dumps(payload_snapshot, ensure_ascii=False)[:12000],
            )

            try:
                response = await session.create_or_continue(
                    input_items=input_items,
                    tools=session_tools if send_tools else None,
                )
                if send_tools and session_tools:
                    session.mark_tools_sent()
            except Exception as exc:
                if self._client_previous_response_not_found(client, exc):
                    await session.close()
                    session = await client.get_ws_session(str(run.id), model_name)
                    input_items = self._build_ws_input_items(
                        history,
                        previous_response_id=getattr(session, "previous_response_id", None),
                        run_id=str(run.id),
                    )
                    continue
                if self._client_is_ws_exception(client, exc):
                    await client.close_ws_session(str(run.id), model=model_name)
                    raise
                raise

            await self._record_response_id(run, response.get("response_id"))

            assistant_text = response.get("text") or ""
            tool_calls = response.get("tool_calls") or []
            if not tool_calls and assistant_text:
                synthesized_tool_calls = extract_code_like_tool_calls(assistant_text, allowed_tool_names)
                if synthesized_tool_calls:
                    logger.warning(
                        "Repaired code-like tool output run=%s provider=%s model=%s tool_names=%s",
                        run.id,
                        provider,
                        model_name,
                        [call.get("name") for call in synthesized_tool_calls],
                    )
                    tool_calls = synthesized_tool_calls
                    assistant_text = ""
            final_text = assistant_text
            await self._persist_message(
                run,
                MessageRole.ASSISTANT,
                assistant_text,
                meta={
                    "raw": response.get("raw"),
                    "tool_code_repaired": bool(tool_calls and not assistant_text),
                },
            )
            assistant_entry = {"role": "assistant", "content": assistant_text or ""}
            if tool_calls:
                converted_tool_calls = []
                for call in tool_calls:
                    args_raw = call.get("arguments") or {}
                    if isinstance(args_raw, str):
                        arguments = args_raw
                    else:
                        arguments = json.dumps(args_raw, ensure_ascii=False)
                    converted_tool_calls.append(
                        {
                            "id": call.get("id"),
                            "type": "function",
                            "function": {
                                "name": call.get("name"),
                                "arguments": arguments,
                            },
                        }
                    )
                assistant_entry["tool_calls"] = converted_tool_calls
            history.append(assistant_entry)

            if tool_calls and tools:
                for call in tool_calls:
                    tool_name = (call.get("name") or "").strip()
                    call_id = _call_id_value(call)
                    args_raw = call.get("arguments") or "{}"
                    args_json: dict[str, Any] = {}
                    parse_error: str | None = None
                    if isinstance(args_raw, str):
                        try:
                            args_json = json.loads(args_raw)
                        except json.JSONDecodeError:
                            parse_error = "invalid_tool_call_arguments"
                    elif isinstance(args_raw, dict):
                        args_json = args_raw
                    else:
                        parse_error = "invalid_tool_call_arguments"

                    if not tool_name:
                        parse_error = "invalid_tool_call_missing_name"

                    tool_call_obj = await sync_to_async(LLMToolCall.objects.create)(
                        run=run,
                        tool_name=tool_name or "unknown_tool",
                        arguments=args_json,
                    )
                    if parse_error:
                        result_payload = {"ok": False, "error": parse_error}
                        success = False
                        error_txt = parse_error
                    else:
                        tool_orch_id = orchestration_run_id or str(run.id)
                        result_payload = await self._execute_tool(tool_name, args_json, tool_orch_id)
                        success = result_payload.get("ok", False)
                        error_txt = result_payload.get("error") or ""

                    await sync_to_async(self._finalize_tool_call)(
                        tool_call_obj,
                        success=success,
                        result=result_payload.get("result"),
                        error=error_txt,
                    )

                    tool_message_content = json.dumps(result_payload, ensure_ascii=False)
                    await self._persist_message(
                        run,
                        MessageRole.TOOL,
                        tool_message_content or "",
                        name=tool_name,
                        meta={"tool_call_id": tool_call_obj.id},
                    )
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "provider_call_id": call_id,
                            "content": tool_message_content or "",
                        }
                    )
                    tool_call_count += 1

                tool_rounds += 1
                if tool_rounds > max_tool_rounds:
                    await client.close_ws_session(str(run.id), model=model_name)
                    await self._finalize_run(
                        run,
                        RunStatus.FAILED,
                        error="max_tool_rounds_exceeded",
                        usage=usage_totals,
                        error_meta={"error_type": "ToolRoundLimit", "error": "max_tool_rounds_exceeded"},
                    )
                    return {
                        "run_id": str(run.id),
                        "text": "",
                        "tool_calls_executed": tool_call_count,
                        "status": "failed",
                        "error": "max_tool_rounds_exceeded",
                    }
                input_items = self._build_ws_input_items(
                    history,
                    previous_response_id=getattr(session, "previous_response_id", None),
                    run_id=str(run.id),
                )
                session.reset_tools_sent()
                continue

            await client.close_ws_session(str(run.id), model=model_name)
            await self._finalize_run(run, RunStatus.COMPLETED, usage=usage_totals)
            return {
                "run_id": str(run.id),
                "text": final_text,
                "tool_calls_executed": tool_call_count,
                "status": "completed",
                "error": None,
            }

    def _client_previous_response_not_found(self, client, exc: Exception) -> bool:
        checker = getattr(client, "is_previous_response_not_found", None)
        return bool(callable(checker) and checker(exc))

    def _client_is_ws_exception(self, client, exc: Exception) -> bool:
        checker = getattr(client, "is_ws_exception", None)
        return bool(callable(checker) and checker(exc))

    def _client_error_meta(self, client, exc: Exception) -> Dict[str, Any]:
        builder = getattr(client, "build_error_meta", None)
        if callable(builder):
            return builder(exc)
        return {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    def _build_ws_input_items(
        self,
        history: List[Dict[str, Any]],
        *,
        previous_response_id: str | None = None,
        run_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        return build_ws_request_input_items(
            history,
            previous_response_id=previous_response_id,
            include_system_context=True,
            run_id=run_id,
        )

    async def _record_response_id(self, run: LLMRun, response_id: Optional[str]) -> None:
        if not response_id:
            return
        meta = run.provider_meta or {}
        provider_key = str(run.provider or "openai").lower()
        response_ids_key = "provider_response_ids"
        response_id_key = "provider_response_id"
        ids: List[str] = list(meta.get(response_ids_key) or [])
        if response_id:
            if response_id in ids:
                ids.remove(response_id)
            ids.append(response_id)
        meta[response_ids_key] = ids[-10:]
        meta[response_id_key] = response_id
        if provider_key == "openai":
            meta["openai_response_ids"] = ids[-10:]
            meta["openai_response_id"] = response_id
        run.provider_meta = meta
        await sync_to_async(run.save)(update_fields=["provider_meta"])

    async def _execute_tool(self, tool_name: str, args: Dict[str, Any], orchestration_run_id: Optional[str]):
        tool_args = dict(args)
        if tool_name == "repo_tree":
            path_value = tool_args.pop("path", None)
            if path_value is not None:
                tool_args.setdefault("root", path_value)
        return await asyncio.wait_for(
            run_tool(tool_name, tool_args, orchestration_run_id=orchestration_run_id),
            timeout=settings.TOOLRUNNER_TIMEOUT,
        )

    def _finalize_tool_call(self, tool_call: LLMToolCall, *, success: bool, result: Any, error: str):
        tool_call.success = success
        tool_call.result = result
        tool_call.error = error
        tool_call.save(update_fields=["success", "result", "error"])

    async def _finalize_run(
        self,
        run: LLMRun,
        status: str,
        error: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        error_meta: Optional[Dict[str, Any]] = None,
    ):
        run.status = status
        if usage:
            run.token_prompt = usage.get("token_prompt") or 0
            run.token_completion = usage.get("token_completion") or 0
            run.token_total = usage.get("token_total") or 0
        if error:
            run.error = error
        run.provider_meta = run.provider_meta or {}
        if error_meta:
            run.provider_meta.update(error_meta)
        run.provider_meta.update({"ended_at": timezone.now().isoformat()})
        await sync_to_async(run.save)()
