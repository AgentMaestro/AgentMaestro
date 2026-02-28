import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from llm.models import (
    AgentRole,
    LLMMessage,
    LLMModelProfile,
    LLMRun,
    LLMToolCall,
    MessageRole,
    RunStatus,
)
from llm.services.registry import get_client
from llm.services.toolrunner_bridge import run_tool
from llm.services.tool_schemas import get_tool_schemas
from llm.services.providers.openai_client import retry_with_backoff
from llm.services.providers.openai_ws import (
    OpenAIResponsesWSException,
    OpenAIResponsesWSPreviousResponseNotFound,
)



def _call_id_value(call_payload):
    return (
        str(call_payload.get("call_id") or "")
        or str(call_payload.get("id") or "")
        or str(call_payload.get("tool_call_id") or "")
    )



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
        tools: Optional[List[Dict[str, Any]]] = None,
        orchestration_run_id: Optional[str] = None,
        purpose: Optional[str] = None,
        max_tool_rounds: int = 3,
        messages: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        profile = await self._resolve_profile(profile_name, agent_role)
        provider = profile.provider if profile else self.default_provider
        model_name = profile.model if profile else ""
        agent_display = agent_name or (profile.name if profile else "Unnamed")

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
        tool_rounds = 0
        usage_totals = {"token_prompt": 0, "token_completion": 0, "token_total": 0}

        try:
            tools = tools if tools is not None else get_tool_schemas()
            client = get_client(provider)
            transport = os.getenv("OPENAI_TRANSPORT", client.transport).lower()
            if transport == "ws":
                return await self._run_ws_transport(
                    client=client,
                    run=run,
                    history=history,
                    tools=tools,
                    model_name=model_name,
                    orchestration_run_id=orchestration_run_id,
                    max_tool_rounds=max_tool_rounds,
                )
            while True:
                response = await retry_with_backoff(
                    lambda: client.complete(
                        history,
                        model=model_name,
                        tools=tools,
                        temperature=profile.temperature if profile else None,
                        max_output_tokens=profile.max_output_tokens if profile else None,
                        extra=profile.extra if profile else None,
                    ),
                    max_retries=self.max_retries,
                )

                assistant_text = response.get("text") or ""
                tool_calls = response.get("tool_calls") or []
                usage = response.get("usage") or {}

                await self._persist_message(run, MessageRole.ASSISTANT, assistant_text, meta={"raw": response.get("raw")})
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

                if tool_calls and tools:
                    # persist tool calls, run them, add tool messages
                    for call in tool_calls:
                        tool_name = call.get("name") or ""
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
                        tool_name = tool_name.strip()

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
                            tool_call_obj, success=success, result=result_payload.get("result"), error=error_txt
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
                    # continue loop to ask the model again with tool results
                    tool_rounds += 1
                    # guardrail: stop if the model keeps requesting tools beyond the configured limit
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

                # No tool calls; finalize with accumulated usage totals
                await self._finalize_run(run, RunStatus.COMPLETED, usage=usage_totals)
                return {
                    "run_id": str(run.id),
                    "text": assistant_text,
                    "tool_calls_executed": tool_call_count,
                    "status": "completed",
                    "error": None,
                }
        except asyncio.TimeoutError as exc:
            error_message = "TimeoutError"
            error_type = type(exc).__name__
        except Exception as exc:
            error_message = str(exc)
            error_type = type(exc).__name__
        error_meta = {
            "error_type": error_type or "Exception",
            "error": error_message or "",
        }
        await self._finalize_run(
            run, RunStatus.FAILED, error_message, usage=usage_totals, error_meta=error_meta
        )
        return {
            "run_id": str(run.id),
            "text": "",
            "tool_calls_executed": tool_call_count,
            "status": "failed",
            "error": error_message,
        }

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
        self, run: LLMRun, role: str, content: str, *, name: Optional[str] = None, meta: Optional[Dict[str, Any]] = None
    ):
        await sync_to_async(LLMMessage.objects.create)(
            run=run, role=role, content=content or "", name=name or "", meta=meta or {}
        )

    def _normalize_usage(self, usage: Dict[str, Any]) -> Dict[str, int]:
        # Always treat missing token counts as zero to avoid None overwrites
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
        def _call_id_value(call_payload: Dict[str, Any]) -> str:
            return call_payload.get("id") or call_payload.get("call_id") or ""
        await client.cleanup_ws_sessions()
        session = await client.get_ws_session(str(run.id), model_name)
        session_tools = client.format_tool_definitions_for_responses(tools)
        initial_input_items = self._build_ws_input_items(history)
        continuation_instructions = list(initial_input_items)
        input_items = initial_input_items
        send_tools = bool(session_tools)
        final_text = ""
        while True:
            try:
                response = await session.create_or_continue(
                    input_items=input_items,
                    tools=session_tools if send_tools else None,
                )
            except OpenAIResponsesWSPreviousResponseNotFound:
                await session.close()
                session = await client.get_ws_session(str(run.id), model_name)
                initial_input_items = self._build_ws_input_items(history)
                continuation_instructions = list(initial_input_items)
                input_items = initial_input_items
                send_tools = bool(session_tools)
                continue
            except OpenAIResponsesWSException as exc:
                await client.close_ws_session(str(run.id))
                await self._finalize_run(
                    run,
                    RunStatus.FAILED,
                    error=str(exc),
                    usage=usage_totals,
                    error_meta={"error_type": type(exc).__name__, "error": str(exc)},
                )
                return {
                    "run_id": str(run.id),
                    "text": "",
                    "tool_calls_executed": tool_call_count,
                    "status": "failed",
                    "error": str(exc),
                }
            send_tools = False
            await self._record_response_id(run, response.get("response_id"))
            assistant_text = response.get("text") or ""
            final_text = assistant_text
            await self._persist_message(run, MessageRole.ASSISTANT, assistant_text, meta={"raw": response.get("raw")})
            assistant_entry = {"role": "assistant", "content": assistant_text or ""}
            tool_calls = response.get("tool_calls") or []
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
                function_call_outputs: List[Dict[str, str]] = []
                for call in tool_calls:
                    tool_name = call.get("name") or ""
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
                    tool_name = tool_name.strip()

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
                            "content": tool_message_content or "",
                        }
                    )
                    tool_call_count += 1
                    function_call_outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result_payload, ensure_ascii=False),
                        }
                    )
                tool_rounds += 1
                if tool_rounds > max_tool_rounds:
                    await client.close_ws_session(str(run.id))
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
                input_items = function_call_outputs + continuation_instructions
                continue

            await client.close_ws_session(str(run.id))
            await self._finalize_run(run, RunStatus.COMPLETED, usage=usage_totals)
            return {
                "run_id": str(run.id),
                "text": final_text,
                "tool_calls_executed": tool_call_count,
                "status": "completed",
                "error": None,
            }

    def _build_ws_input_items(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for entry in history:
            role = entry.get("role")
            if role not in {"system", "user", "assistant"}:
                continue
            content = (entry.get("content") or "").strip()
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

    async def _record_response_id(self, run: LLMRun, response_id: Optional[str]) -> None:
        if not response_id:
            return
        meta = run.provider_meta or {}
        ids: List[str] = list(meta.get("openai_response_ids") or [])
        if response_id:
            if response_id in ids:
                ids.remove(response_id)
            ids.append(response_id)
        meta["openai_response_ids"] = ids[-10:]
        meta["openai_response_id"] = response_id
        run.provider_meta = meta
        await sync_to_async(run.save)(update_fields=["provider_meta"])


    async def _execute_tool(self, tool_name: str, args: Dict[str, Any], orchestration_run_id: Optional[str]):
        # Run tool with a timeout to avoid runaway calls.
        return await asyncio.wait_for(
            run_tool(tool_name, args, orchestration_run_id=orchestration_run_id),
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
