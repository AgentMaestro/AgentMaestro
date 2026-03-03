from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.services.toolrunner_bridge import run_tool


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] | None = None


class Command(BaseCommand):
    help = "Run the ToolRunner bridge integration and guardrail suite."

    def handle(self, *args: Any, **options: Any):
        results = asyncio.run(self._run_suite())
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            self.stdout.write(f"{status}: {result.name} — {result.message}")
            if result.details:
                self.stdout.write(f"  details: {result.details}")
        if not all(result.passed for result in results):
            raise CommandError("ToolRunner test suite detected failures")

    async def _run_suite(self) -> list[TestResult]:
        backend_root = Path(settings.BASE_DIR).resolve()
        results: list[TestResult] = []
        results.append(await self._test_repo_tree(backend_root))
        results.append(await self._test_search_code(backend_root))
        results.append(await self._test_file_read_allowed(backend_root))
        results.append(await self._test_file_read_blocked(backend_root))
        results.append(await self._test_file_write_and_patch())
        results.append(await self._test_file_write_outside_workspace())
        results.append(await self._test_shell_command_basic())
        results.append(await self._test_shell_command_truncated())
        results.append(await self._test_python_chunked_output())
        results.append(await self._test_python_baseline())
        results.append(await self._test_path_traversal())
        results.append(await self._test_case_insensitive_repo_tree(backend_root))
        results.append(await self._test_unknown_tool())
        return results

    async def _run_tool(self, tool_name: str, args: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        response = await run_tool(tool_name, args)
        tool_result = response.get("result") or {}
        if not isinstance(tool_result, dict):
            tool_result = {}
        return response, tool_result

    async def _test_repo_tree(self, backend_root: Path) -> TestResult:
        args = {
            "absolute_root": str(backend_root),
            "max_depth": 3,
            "include_files": True,
            "include_dirs": True,
        }
        response, tool_result = await self._run_tool("repo_tree", args)
        if not tool_result.get("ok"):
            return TestResult(
                "repo_tree absolute",
                False,
                "repo_tree failed", 
                {"error": tool_result.get("error") or response.get("error")},
            )
        result = tool_result.get("result") or {}
        stats = result.get("stats") or {}
        entries = result.get("entries") or []
        excluded_patterns = stats.get("excluded_patterns") or []
        excluded_matches = stats.get("excluded_matches_by_pattern") or {}
        policy_meta = tool_result.get("meta", {}).get("policy")
        has_git_excluded = any(pattern == "**/.git/**" for pattern in excluded_patterns)
        has_policies = bool(policy_meta)
        no_git_entries = not any(entry.get("path", "").startswith(".git") for entry in entries)
        no_venv_entries = not any(entry.get("path", "").startswith(".venv") for entry in entries)
        passed = has_git_excluded and has_policies and no_git_entries and no_venv_entries and bool(entries)
        details = {
            "entries": len(entries),
            "excluded_matches": excluded_matches,
            "allowed_roots": stats.get("allowed_roots"),
        }
        message = "repo_tree enumerated backend" if passed else "repo_tree guardrail check failed"
        return TestResult("repo_tree", passed, message, details)

    async def _test_search_code(self, backend_root: Path) -> TestResult:
        args = {
            "absolute_root": str(backend_root),
            "query": "import",
            "include_globs": ["**/*.py"],
            "max_results": 2,
        }
        response, tool_result = await self._run_tool("search_code", args)
        if not tool_result.get("ok"):
            return TestResult(
                "search_code truncated",
                False,
                "search_code failed",
                {"error": tool_result.get("error") or response.get("error")},
            )
        result = tool_result.get("result") or {}
        stats = result.get("stats") or {}
        files_scanned = stats.get("files_scanned", 0)
        truncated = result.get("truncated") is True
        policy_meta = tool_result.get("meta", {}).get("policy")
        passed = files_scanned > 0 and truncated and bool(policy_meta)
        details = {
            "files_scanned": files_scanned,
            "truncated": truncated,
        }
        message = "search_code scanned backend and truncated results" if passed else "search_code guardrail failure"
        return TestResult("search_code", passed, message, details)

    async def _test_file_read_allowed(self, backend_root: Path) -> TestResult:
        args = {
            "path": "manage.py",
            "absolute_root": str(backend_root),
            "mode": "text",
            "encoding": "utf-8",
            "max_bytes": 4096,
        }
        _, tool_result = await self._run_tool("file_read", args)
        content = tool_result.get("result", {}).get("content", "")
        passed = tool_result.get("ok") and "DJANGO_SETTINGS_MODULE" in content
        return TestResult(
            "file_read allowed",
            passed,
            "manage.py is readable" if passed else "unable to read manage.py",
            {"ok": tool_result.get("ok")},
        )

    async def _test_file_read_blocked(self, backend_root: Path) -> TestResult:
        args = {
            "path": ".env",
            "absolute_root": str(backend_root),
            "mode": "text",
            "encoding": "utf-8",
            "max_bytes": 4096,
        }
        response, tool_result = await self._run_tool("file_read", args)
        error_code = tool_result.get("error", {}).get("code") or response.get("error")
        allowed = bool(tool_result.get("meta", {}).get("policy"))
        passed = not tool_result.get("ok") and isinstance(error_code, str) and "PATH_EXCLUDED" in error_code
        details = {"error": error_code, "policy": allowed}
        return TestResult("file_read excluded", passed, "blocked .env read", details)

    async def _test_file_write_and_patch(self) -> TestResult:
        write_args = {
            "path": "smoke/smoke_body.txt",
            "content": "hello sandbox\n",
            "mode": "text",
            "overwrite": True,
        }
        _, write_result = await self._run_tool("file_write", write_args)
        if not write_result.get("ok"):
            return TestResult("file_write sandbox", False, "write failed", write_result.get("error"))
        patch_text = """--- a/smoke/smoke_body.txt
+++ b/smoke/smoke_body.txt
@@ -1 +1 @@
-hello sandbox
+hello patched
"""
        patch_args = {
            "path": "smoke/smoke_body.txt",
            "patch_unified": patch_text,
        }
        _, patch_result = await self._run_tool("file_patch", patch_args)
        patched = patch_result.get("result", {}).get("applied") is True
        return TestResult("file_patch sandbox", patched, "patch applied", {"patched": patched})

    async def _test_file_write_outside_workspace(self) -> TestResult:
        args = {
            "path": "../forbidden.txt",
            "content": "nope\n",
            "mode": "text",
            "overwrite": True,
        }
        response, tool_result = await self._run_tool("file_write", args)
        error_code = tool_result.get("error", {}).get("code") or response.get("error")
        passed = not tool_result.get("ok") and isinstance(error_code, str)
        details = {"error": error_code}
        return TestResult("file_write outside sandbox", passed, "write denied", details)

    async def _test_shell_command_basic(self) -> TestResult:
        args = {"cmd": ["cmd", "/c", "dir"], "cwd": "."}
        response, tool_result = await self._run_tool("shell_exec", args)
        result = tool_result.get("result", {})
        passed = tool_result.get("ok") and result.get("exit_code") == 0
        return TestResult("shell_exec dir", passed, "basic dir command", {"stdout": bool(result.get("stdout"))})

    async def _test_shell_command_truncated(self) -> TestResult:
        args = {"cmd": ["cmd", "/c", "for /L %i in (1,1,200) do @echo line %i"], "cwd": "."}
        response, tool_result = await self._run_tool("shell_exec", args)
        result = tool_result.get("result", {})
        passed = tool_result.get("ok") and result.get("stdout_truncated") is True
        return TestResult("shell_exec truncated", passed, "long output truncated", {"stdout_truncated": result.get("stdout_truncated")})

    async def _test_python_baseline(self) -> TestResult:
        args = {"code": "print('hi')"}
        _, tool_result = await self._run_tool("python_exec", args)
        result = tool_result.get("result", {})
        passed = tool_result.get("ok") and result.get("stdout", "").strip() == "hi"
        return TestResult("python_exec baseline", passed, "python hello", {"stdout": result.get("stdout")})

    async def _test_python_chunked_output(self) -> TestResult:
        code = "for i in range(400):\n    print('line', i)\n"
        args = {"code": code}
        _, tool_result = await self._run_tool("python_exec", args)
        result = tool_result.get("result", {})
        stdout = result.get("stdout", "")
        truncated = stdout.endswith("â€¦")
        passed = tool_result.get("ok") and truncated
        return TestResult("python_exec truncated", passed, "python output truncated", {"truncated": truncated})

    async def _test_path_traversal(self) -> TestResult:
        args = {"path": "../traverse.txt", "mode": "text", "encoding": "utf-8", "max_bytes": 256}
        response, tool_result = await self._run_tool("file_read", args)
        error_code = tool_result.get("error", {}).get("code") or response.get("error")
        passed = not tool_result.get("ok") and isinstance(error_code, str) and "PATH_OUTSIDE_WORKSPACE" in error_code
        return TestResult("path traversal", passed, "path traversal blocked", {"error": error_code})

    async def _test_case_insensitive_repo_tree(self, backend_root: Path) -> TestResult:
        weird = str(backend_root).upper()
        args = {
            "absolute_root": weird,
            "max_depth": 1,
            "include_files": False,
            "include_dirs": True,
        }
        response, tool_result = await self._run_tool("repo_tree", args)
        passed = tool_result.get("ok") is True
        return TestResult(
            "repo_tree case tolerance",
            passed,
            "case-insensitive allowlist",
            {"error": tool_result.get("error") or response.get("error")},
        )

    async def _test_unknown_tool(self) -> TestResult:
        response, tool_result = await self._run_tool("tool_missing", {})
        error_code = tool_result.get("error") or response.get("error")
        passed = not tool_result.get("ok") and isinstance(error_code, str)
        return TestResult("unknown tool", passed, "unknown tool rejected", {"error": error_code})
