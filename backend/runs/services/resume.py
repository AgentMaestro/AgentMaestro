from __future__ import annotations

import logging
from typing import Iterable, List

from django.conf import settings
from llm.models import LLMModelProfile
from runs.models import AgentRun, RunEvent
from runs.services.event_builders import build_assistant_message_payload
from runs.services.events import append_event
from tools.models import ToolCall
from tools.policy import get_effective_tools
from tools.services.approvals import request_tool_call_approval

logger = logging.getLogger(__name__)


def resolve_provider_and_model(agent) -> tuple[str, str]:
    profile = (
        LLMModelProfile.objects.filter(name=agent.policy_name, is_active=True)
        .order_by("name")
        .first()
    )
    provider = profile.provider if profile else getattr(settings, "LLM_PROVIDER", "openai")
    model_name = profile.model if profile else agent.default_model
    return provider, model_name


def build_tool_payloads(agent, user) -> list[dict] | None:
    try:
        effective_tools = get_effective_tools(agent, user)
    except Exception:
        logger.exception("Failed to load effective tools for agent=%s", agent.slug)
        return None
    if not effective_tools:
        return None
    payloads: list[dict] = []
    for entry in effective_tools:
        payloads.append(
            {
                "name": entry.tool.name,
                "description": entry.description,
                "parameters": entry.args_schema or {},
            }
        )
    return payloads


def persist_assistant_response(
    run: AgentRun,
    text: str,
    *,
    model: str | None = None,
    provider_response_id: str | None = None,
) -> None:
    if not text:
        logger.debug("No assistant text to persist for run=%s", run.id)
        return
    last_event = (
        RunEvent.objects.filter(run=run, event_type="assistant_message")
        .order_by("-seq")
        .first()
    )
    if (
        last_event
        and last_event.payload.get("content") == text
        and last_event.payload.get("provider_response_id") == provider_response_id
    ):
        logger.debug(
            "Skipping duplicate assistant message for run=%s content=%s",
            run.id,
            text,
        )
        return
    payload = build_assistant_message_payload(
        text,
        model=model,
        provider_response_id=provider_response_id,
        step_index=getattr(run, "current_step_index", None),
    )
    append_event(
        run_id=str(run.id),
        event_type="assistant_message",
        payload=payload,
        correlation_id=run.correlation_id,
    )


def handle_provider_tool_calls(
    run_id: str,
    tool_calls: Iterable[dict],
    *,
    run: AgentRun,
) -> None:
    for entry in tool_calls or []:
        tool_name = entry.get("name") or entry.get("tool") or ""
        if not tool_name:
            logger.warning("Skipping tool call with missing name run=%s", run_id)
            continue
        args = entry.get("arguments") or {}
        provider_call_id = str(entry.get("call_id") or entry.get("id") or "")
        if provider_call_id and ToolCall.objects.filter(provider_call_id=provider_call_id).exists():
            logger.info(
                "Skipping duplicate resumed tool call run=%s provider_call_id=%s",
                run_id,
                provider_call_id,
            )
            continue
        try:
            tool_call = request_tool_call_approval(
                run_id=run_id,
                tool_name=tool_name,
                args=args,
                requires_approval=False,
            )
        except Exception:
            logger.exception(
                "Failed to request approval for tool_call=%s run=%s", tool_name, run_id
            )
            continue
        if provider_call_id:
            ToolCall.objects.filter(id=tool_call.id).update(provider_call_id=provider_call_id)
        logger.info(
            "Queued resumed provider tool call run=%s tool_call=%s provider_call_id=%s",
            run_id,
            tool_call.id,
            provider_call_id,
        )
