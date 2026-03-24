from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand
from logging_utils import scrub_sensitive_text
from unittest.mock import patch

from llm.models import AgentRole, LLMModelProfile, LLMRun
from llm.services.runner import LLMRunner
from llm.services.tool_schema import get_default_tools


class FakeClient:
    def __init__(self, responses: Sequence[Dict[str, Any]]):
        self._responses = list(responses)

    async def complete(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if self._responses:
            return self._responses.pop(0)
        return {"text": "done", "tool_calls": [], "usage": {}}


async def identity_retry(func, **kwargs):
    return await func()


LLM_TOOL_LIST = get_default_tools()


def _make_repo_tree_responses(backend_root: str) -> List[Dict[str, Any]]:
    return [
        {
            "text": "Inspecting repository layout.",
            "tool_calls": [
                {
                    "id": "repo-tree",
                    "name": "repo_tree",
                    "arguments": {
                        "root": ".",
                        "absolute_root": backend_root,
                        "max_depth": 3,
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": (
                "The Django backend contains directories core, agents, runs, tools, ui, api, llm, comms, control plus manage.py and pyproject.toml."
            ),
            "tool_calls": [],
            "usage": {},
        },
    ]


def _make_search_code_responses(settings_path: str) -> List[Dict[str, Any]]:
    return [
        {
            "text": "Searching for the ToolRunner URL configuration.",
            "tool_calls": [
                {
                    "id": "search-url",
                    "name": "search_code",
                    "arguments": {
                        "query": "AGENTMAESTRO_TOOLRUNNER_URL",
                        "include_globs": ["**/*.py"],
                        "root": ".",
                        "max_results": 5,
                        "case_sensitive": False,
                        "is_regex": False,
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": "Reading the settings file referenced by the search.",
            "tool_calls": [
                {
                    "id": "read-settings",
                    "name": "file_read",
                    "arguments": {"path": settings_path},
                }
            ],
            "usage": {},
        },
        {
            "text": (
                "AGENTMAESTRO_TOOLRUNNER_URL is derived from TOOLRUNNER_BASE_URL under agentmaestro/settings/base.py, "
                "so editing BASE_URL implicitly updates the bridge endpoint."
            ),
            "tool_calls": [],
            "usage": {},
        },
    ]


def _make_patch_responses() -> List[Dict[str, Any]]:
    patch_text = """--- a/sandbox_payload.json
++ b/sandbox_payload.json
@@ -1 +1 @@
-{"field": "initial"}
+{"field": "updated"}
"""
    return [
        {
            "text": "Creating a sandbox payload file.",
            "tool_calls": [
                {
                    "id": "write-payload",
                    "name": "file_write",
                    "arguments": {
                        "path": "sandbox_payload.json",
                        "content": '{"field": "initial"}',
                        "mode": "text",
                        "make_dirs": True,
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": "Applying a patch to update the JSON payload.",
            "tool_calls": [
                {
                    "id": "patch-payload",
                    "name": "file_patch",
                    "arguments": {
                        "path": "sandbox_payload.json",
                        "patch_unified": patch_text,
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": "Confirming the patched payload.",
            "tool_calls": [
                {
                    "id": "read-payload",
                    "name": "file_read",
                    "arguments": {"path": "sandbox_payload.json"},
                }
            ],
            "usage": {},
        },
        {
            "text": "The sandbox file now contains {'field': 'updated'}.",
            "tool_calls": [],
            "usage": {},
        },
    ]


def _make_shell_responses() -> List[Dict[str, Any]]:
    return [
        {
            "text": "Listing sandbox entries for JSON parsing.",
            "tool_calls": [
                {
                    "id": "shell-list",
                    "name": "shell_exec",
                    "arguments": {
                        "cmd": ["powershell", "-NoProfile", "-Command", "Get-ChildItem -Name"],
                        "cwd": ".",
                        "env": {},
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": "Parsed the command output into a JSON array containing the sandbox contents.",
            "tool_calls": [],
            "usage": {},
        },
    ]


def _make_python_responses() -> List[Dict[str, Any]]:
    return [
        {
            "text": "Computing a deterministic SHA256 via python_exec.",
            "tool_calls": [
                {
                    "id": "python-sha",
                    "name": "python_exec",
                    "arguments": {
                        "code": "import hashlib\nprint(hashlib.sha256(b'AgentMaestro').hexdigest())",
                    },
                }
            ],
            "usage": {},
        },
        {
            "text": "The SHA256 of 'AgentMaestro' is 424f09b4c86a9a53b1a6fc497fcba98b6cc8c6c9cb7a9f700fd9c0549a4a1d5f.",
            "tool_calls": [],
            "usage": {},
        },
    ]


SCENARIOS = [
    (
        "Repository orientation",
        "Inspect the Django backend repo by calling repo_tree with depth 3, identify directories and apps, and summarize.",
        _make_repo_tree_responses(str(settings.BASE_DIR)),
    ),
    (
        "Targeted code lookup",
        "Find where AGENTMAESTRO_TOOLRUNNER_URL is defined, use search_code and file_read, and summarize the setting.",
        _make_search_code_responses("agentmaestro/settings/base.py"),
    ),
    (
        "Sandbox patch flow",
        "Create a JSON payload inside the sandbox, patch one field, and read it back to confirm.",
        _make_patch_responses(),
    ),
    (
        "Shell + parse",
        "Run a shell_exec command that lists sandbox entries and return those names as JSON.",
        _make_shell_responses(),
    ),
    (
        "Python exec compute",
        "Use python_exec to compute the SHA256 of a fixed string and report that value.",
        _make_python_responses(),
    ),
]


@dataclass
class ScenarioResult:
    name: str
    run_id: str
    tool_calls_executed: int
    final_output: str
    policy_denials: List[str]
    status: str


class Command(BaseCommand):
    help = "Runs Maestro-style LLM prompts to exercise the ToolRunner bridge."

    def handle(self, *args: Any, **options: Any) -> None:
        profile = self._ensure_profile()
        for scenario_name, prompt, responses in SCENARIOS:
            result = self._run_scenario(scenario_name, prompt, responses, profile)
            self.stdout.write(scrub_sensitive_text(f"\nScenario: {scenario_name}"))
            self.stdout.write(scrub_sensitive_text(f"  run_id: {result.run_id}"))
            self.stdout.write(scrub_sensitive_text(f"  status: {result.status}"))
            self.stdout.write(scrub_sensitive_text(f"  tool_calls_executed: {result.tool_calls_executed}"))
            self.stdout.write(scrub_sensitive_text(f"  final output: {result.final_output}"))
            if result.policy_denials:
                for denial in result.policy_denials:
                    self.stdout.write(scrub_sensitive_text(f"  policy denial: {denial}"))
            else:
                self.stdout.write("  policy denials: none")

    def _ensure_profile(self) -> LLMModelProfile:
        profile, created = LLMModelProfile.objects.get_or_create(
            name="Maestro",
            defaults={
                "agent_role": AgentRole.PLANNER,
                "provider": "openai",
                "model": "test-model",
                "is_active": True,
            },
        )
        if created:
            profile.save()
        return profile

    def _run_scenario(
        self,
        name: str,
        prompt: str,
        responses: Sequence[Dict[str, Any]],
        profile: LLMModelProfile,
    ) -> ScenarioResult:
        runner = LLMRunner()
        fake_client = FakeClient(responses)
        with patch("llm.services.runner.get_client", lambda provider: fake_client), patch(
            "llm.services.runner.retry_with_backoff", new=identity_retry
        ):
            result = asyncio.run(
                runner.run(
                    prompt=prompt,
                    profile_name=profile.name,
                    tools=LLM_TOOL_LIST,
                    max_tool_rounds=5,
                )
            )
        run = LLMRun.objects.get(id=result["run_id"])
        denials = [
            tool_call.error
            for tool_call in run.tool_calls.filter(error__startswith="tool_runner")
            if tool_call.error
        ]
        return ScenarioResult(
            name=name,
            run_id=str(run.id),
            tool_calls_executed=result["tool_calls_executed"],
            final_output=result["text"],
            policy_denials=denials,
            status=result["status"],
        )
