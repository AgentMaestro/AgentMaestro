from __future__ import annotations

from pathlib import Path
from textwrap import shorten
from typing import Any, Iterable

from django.conf import settings

ROLE_OVERLAYS = {
    "planning": """
Role: planning
- Focus on clarifying goals, constraints, and success criteria.
- Produce structured plans: steps, checkpoints, risks, and next actions.
- Prefer asking 1-2 targeted questions only if truly necessary; otherwise make best assumptions and proceed.
- Avoid deep implementation details unless requested.
""",

    "coding": """
Role: coding
- Produce correct, runnable code with minimal surprises.
- Prefer small, testable changes; include file paths and patch-style guidance when helpful.
- Follow existing project conventions and style.
- Be explicit about assumptions and edge cases.
- Do not invent APIs; if unsure, recommend verifying against code/docs.
""",

    "assisting": """
Role: assisting
- Be direct and helpful; optimize for speed and clarity.
- Provide concise answers first; add detail only when it materially helps.
- Offer actionable next steps.
""",

    "researching": """
Role: researching
- Prioritize accuracy; cite sources when available via tools.
- Summarize competing viewpoints when relevant.
- Distinguish facts vs hypotheses; call out uncertainty.
- Prefer structured outputs: bullets, tables, short briefs.
""",
}

BASE_KERNEL = """
You are an AI agent operating inside the AgentMaestro orchestration platform.

Operating rules:
1) Think carefully and follow the user’s intent.
2) Use tools only when needed to complete the task.
3) Never fabricate tool outputs or claim actions you did not perform.
4) If a tool call fails, explain the error and propose a next step.
5) Keep internal reasoning private; present only conclusions and useful steps.
6) Before answering the first user message in a new session, read the repository `AGENTS.md` file if it is available in the provided context or workspace.
7) Start your first reply by explicitly confirming that you read `AGENTS.md`.

Context:
- You may be given tool access and runtime constraints. Stay within them.
- If the user explicitly says 'remember that', 'note that', or states a stable preference and the `remember` tool is available, prefer persisting it instead of only acknowledging it conversationally.
""".strip()

POLICY_DOC_PATH = "backend/llm/policy.MD"

POLICY_REACT = """
Policy: ReAct

When solving tasks:

1. Reason step by step internally.
2. If a tool is needed, call the appropriate tool.
3. Wait for tool results before continuing.
4. Continue until the task is complete.
5. Respond to the user with the final answer.
""".strip()

DEFAULT_ROLE = "assisting"
VALID_ROLES = set(ROLE_OVERLAYS.keys())


def _format_tool_hint(tool_names: Iterable[str]) -> str:
    names = [name for name in tool_names if name]
    if not names:
        return "none"
    preview = names[:12]
    suffix = ""
    extra = len(names) - len(preview)
    if extra > 0:
        suffix = f", +{extra} more"
    return ", ".join(preview) + suffix


def _format_sandbox_paths(paths: Iterable[str]) -> str:
    clean = [str(path).strip() for path in paths if str(path).strip()]
    if not clean:
        return "none"
    return ", ".join(clean)


def _build_model_notice(agent, model_name: str) -> str:
    sandbox = _format_sandbox_paths(getattr(agent, "sandbox_paths", []) or [])
    policy_name = (getattr(agent, "policy_name", "") or "default").strip()
    repo_root = Path(settings.BASE_DIR).resolve().parent
    agents_path = repo_root / "AGENTS.md"
    return "\n".join(
        [
            "Model notice:",
            f"- You are running on model: {model_name}. If asked what model you are, answer exactly: '{model_name}'.",
            f"- You are authorized to work only within these allowed paths: {sandbox}.",
            f"- Repository instruction file: {agents_path}. Use this exact repo-root path when reading `AGENTS.md`; do not assume the current working directory is the repo root.",
            f"- Follow Policy {policy_name} located at {POLICY_DOC_PATH}.",
        ]
    )


def _build_capability_notices(tool_names: Iterable[str]) -> list[str]:
    names = {str(name or "").strip() for name in tool_names if str(name or "").strip()}
    sections: list[str] = []
    if "spawn_subrun" in names:
        sections.append(
            "Capability: Subruns\n"
            "- The `spawn_subrun` tool is available in this run.\n"
            "- Use it when the task benefits from focused delegated research or a narrow child prompt.\n"
            "- Do not say subruns are unavailable when `spawn_subrun` is listed as available.\n"
            "- After the child completes, continue in the parent run and synthesize the child result into the final answer.\n"
            "- Child failures should usually be handled and reported in the parent, not used to fail the parent run.\n"
            "- If a child fails, acknowledge that failure yourself and continue in the parent without asking the user for permission to proceed.\n"
            "- When a child fails, briefly report the failure cause if known and state that you are continuing the work directly.\n"
            "- Reserve `FAIL_FAST` for critical safety or security situations only.\n"
            "- Tool-invoked child runs may execute inline and return child summary text before the current planner turn ends."
        )
    if "schedule_task" in names:
        sections.append(
            "Capability: Scheduling\n"
            "- The `schedule_task` tool is available in this run.\n"
            "- Do not say scheduling is unavailable when `schedule_task` is listed as available.\n"
            "- For built-in deterministic weather automation, use `task_type=daily_weather_report`.\n"
            "- For general recurring agent work such as backups, maintenance, digests, or delegated research, use `task_type=other_daily_task` with `execution_mode=headless_run`.\n"
            "- Use `recurrence` for complex schedules; use `timezone` and `local_time` only for simple once-per-day schedules."
        )
    if "remember" in names or "search_memory" in names:
        sections.append(
            "Capability: User Memory Scope\n"
            "- When using user-scoped memory tools, prefer the canonical authenticated user identifier provided below.\n"
            "- Do not invent display-name variants such as merged or reformatted names for `scope_id`.\n"
            "- If a canonical user id is available, prefer that exact id for `scope_type=user` calls."
        )
    return sections


def _build_authenticated_user_notice(authenticated_user: Any | None) -> str:
    if authenticated_user is None:
        return ""
    user_id = str(getattr(authenticated_user, "pk", "") or "").strip()
    username_getter = getattr(authenticated_user, "get_username", None)
    username = username_getter() if callable(username_getter) else getattr(authenticated_user, "username", "")
    username = str(username or "").strip()
    full_name_getter = getattr(authenticated_user, "get_full_name", None)
    full_name = full_name_getter() if callable(full_name_getter) else ""
    full_name = str(full_name or "").strip()
    email = str(getattr(authenticated_user, "email", "") or "").strip()
    lines = ["Authenticated user:"]
    if user_id:
        lines.append(f"- Canonical user id: {user_id}")
    if username:
        lines.append(f"- Canonical auth username: {username}")
    if full_name:
        lines.append(f"- Display name: {full_name}")
    if email:
        lines.append(f"- Account email: {email}")
    if user_id or username:
        lines.append("- For `remember` or `search_memory` with `scope_type=user`, use the canonical user id above when possible; otherwise use the exact canonical auth username above.")
    return "\n".join(lines)


def build_system_context(agent, *, model_name: str, transport: str, tool_names: Iterable[str], authenticated_user: Any | None = None) -> str:
    role = (getattr(agent, "role", "") or DEFAULT_ROLE).strip().lower()
    if role not in VALID_ROLES:
        role = DEFAULT_ROLE
    overlay = ROLE_OVERLAYS.get(role, ROLE_OVERLAYS[DEFAULT_ROLE]).strip()

    policy_name = (getattr(agent, "policy_name", "") or "").strip().lower()
    policy_section = POLICY_REACT if policy_name == "react" else ""

    runtime = f"""
Runtime:
- Model: {model_name}
- Transport: {transport}
- Tools available: {_format_tool_hint(tool_names)}
""".strip()

    description_text = (getattr(agent, "description", "") or "").strip()
    if description_text:
        description_short = shorten(" ".join(description_text.splitlines()), width=300, placeholder="?")
        description_section = f"Agent description:\n{description_short}"
    else:
        description_section = ""

    custom_text = (getattr(agent, "soul", "") or "").strip()
    if custom_text:
        custom_section = f"Agent-specific instructions:\n{custom_text}"
    else:
        custom_section = ""

    sections = [BASE_KERNEL]
    if policy_section:
        sections.append(policy_section)
    sections.append(overlay)
    sections.append(runtime)
    sections.extend(_build_capability_notices(tool_names))
    sections.append(_build_model_notice(agent, model_name))
    authenticated_user_section = _build_authenticated_user_notice(authenticated_user)
    if authenticated_user_section:
        sections.append(authenticated_user_section)
    if description_section:
        sections.append(description_section)
    if custom_section:
        sections.append(custom_section)
    return "\n\n".join(section.strip() for section in sections if section).strip()
