from __future__ import annotations

from textwrap import shorten
from typing import Iterable

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
You are Maestro, an AI agent operating inside the AgentMaestro orchestration platform.

Operating rules:
1) Think carefully and follow the user’s intent.
2) Use tools only when needed to complete the task.
3) Never fabricate tool outputs or claim actions you did not perform.
4) If a tool call fails, explain the error and propose a next step.
5) Keep internal reasoning private; present only conclusions and useful steps.

Context:
- You may be given tool access and runtime constraints. Stay within them.
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
    return "\n".join(
        [
            "Model notice:",
            f"- You are running on model: {model_name}. If asked what model you are, answer exactly: '{model_name}'.",
            f"- You are authorized to work only in the sandbox area at {sandbox}.",
            f"- Follow Policy {policy_name} located at {POLICY_DOC_PATH}.",
        ]
    )


def build_system_context(agent, *, model_name: str, transport: str, tool_names: Iterable[str]) -> str:
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

    custom_text = (getattr(agent, "soul", "") or "").strip()
    if custom_text:
        custom_short = shorten(" ".join(custom_text.splitlines()), width=450, placeholder="…")
        custom_section = f"Agent-specific instructions:\n{custom_short}"
    else:
        custom_section = ""

    sections = [BASE_KERNEL]
    if policy_section:
        sections.append(policy_section)
    sections.append(overlay)
    sections.append(runtime)
    sections.append(_build_model_notice(agent, model_name))
    if custom_section:
        sections.append(custom_section)
    return "\n\n".join(section.strip() for section in sections if section).strip()
