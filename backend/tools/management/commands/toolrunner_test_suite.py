"""
Management command to verify ToolRunner bridge integration.

This command sends signed HTTP requests to the ToolRunner FastAPI bridge
and validates that each of the key tools behaves as expected when run via
the Django backend. It is intentionally self-contained so it can be called
manually without needing a running LLM or orchestrator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Runs ToolRunner bridge integration checks without executing the full LLM workflow."

    def handle(self, *args: Any, **options: Any) -> None:
        sandbox_root = Path(os.environ.get("TOOLRUNNER_SANDBOX_ROOT", "C:/tmp/agentmaestro/sandbox")).resolve()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        backend_root = Path(settings.BASE_DIR)
        workspace_id = uuid.uuid4().hex
        run_id = uuid.uuid4().hex
        run_dir = sandbox_root / workspace_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._populate_workspace(run_dir, backend_root)

        escape_workspace_id = ".."
        escape_run_id = "repo-path-write"
        escape_dir = (sandbox_root / escape_workspace_id / escape_run_id).resolve()
        escape_dir.mkdir(parents=True, exist_ok=True)

        results: list[tuple[str, bool, str]] = []
        try:
            results.append(self._run_repo_tree_test(backend_root))
            results.append(self._run_search_code_test(run_dir, workspace_id, run_id))
            results.extend(self._run_file_read_tests(run_dir, workspace_id, run_id))
            results.extend(self._run_file_write_patch_tests(run_dir, workspace_id, run_id, escape_workspace_id, escape_run_id, escape_dir))
            results.extend(self._run_shell_and_python_tests(run_dir, workspace_id, run_id))
        finally:
            # Keep the sandbox artifacts tidy for future runs.
            shutil.rmtree(run_dir, ignore_errors=True)
            shutil.rmtree(escape_dir, ignore_errors=True)

        failures = [msg for name, success, msg in results if not success]
        for name, success, msg in results:
            style = self.style.SUCCESS if success else self.style.ERROR
            self.stdout.write(style(f"{name}: {msg}"))
        if failures:
            raise CommandError("Some ToolRunner bridge checks failed; see output above.")

    def _call_tool(
        self,
        workspace_id: str,
        run_id: str,
        tool_name: str,
        args: dict[str, Any],
        policy: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "run_id": run_id,
            "tool_name": tool_name,
            "args": args,
        }
        if policy is not None:
            payload["policy"] = policy
        limits_payload = dict(limits or {})
        limits_payload.setdefault("timeout_s", settings.TOOLRUNNER_TIMEOUT)
        limits_payload.setdefault("max_output_bytes", settings.TOOLRUNNER_OUTPUT_LIMIT)
        payload["limits"] = limits_payload
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        timestamp = str(int(time.time()))
        message = timestamp.encode("utf-8") + b"." + body
        signature = hmac.new(
            settings.TOOLRUNNER_SECRET.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "X-AM-Timestamp": timestamp,
            "X-AM-Signature": signature,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=settings.TOOLRUNNER_HTTP_TIMEOUT) as client:
                response = client.post(settings.TOOLRUNNER_URL, content=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - best effort logging
            err_msg = f"ToolRunner returned {exc.response.status_code}"
            self.stderr.write(self.style.ERROR(f"[toolrunner_test_suite] {tool_name} {err_msg}"))
            self.stderr.write(self.style.ERROR(f"[toolrunner_test_suite] Request body: {body.decode('utf-8', errors='ignore')}"))
            self.stderr.write(self.style.ERROR(f"[toolrunner_test_suite] Response body: {exc.response.text}"))
            return {
                "ok": False,
                "error": {"message": err_msg, "status_code": exc.response.status_code, "body": exc.response.text},
            }
        except httpx.RequestError as exc:  # pragma: no cover
            err_msg = f"ToolRunner request failed: {exc}"
            self.stderr.write(self.style.ERROR(f"[toolrunner_test_suite] {tool_name} {err_msg}"))
            return {
                "ok": False,
                "error": {"message": err_msg},
            }

        data = response.json()
        status = data.get("status")
        result_payload = data.get("result") or {}
        tool_result = result_payload.get("tool_result")
        payload: dict[str, Any] = {
            "ok": status == "COMPLETED",
            "result": None,
            "error": None,
            "meta": {},
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
        }
        if tool_result is not None:
            payload["ok"] = tool_result.get("ok", status == "COMPLETED")
            payload["result"] = tool_result.get("result")
            payload["error"] = tool_result.get("error")
            payload["meta"] = tool_result.get("meta", {}) or {}
        else:
            payload["result"] = result_payload
            payload["error"] = result_payload.get("error") or None
            policy_meta = result_payload.get("policy")
            if policy_meta:
                payload["meta"]["policy"] = policy_meta
            if not payload["error"] and status != "COMPLETED" and data.get("stderr"):
                payload["error"] = {"message": data.get("stderr")}
        if payload["meta"].get("policy") is None and result_payload.get("policy"):
            payload["meta"]["policy"] = result_payload.get("policy")
        if payload["meta"].get("policy") is None and tool_result and tool_result.get("meta"):
            payload["meta"]["policy"] = tool_result.get("meta", {}).get("policy")
        return payload

    def _populate_workspace(self, run_dir: Path, backend_root: Path) -> None:
        shutil.copy(backend_root / "manage.py", run_dir / "manage.py")
        (run_dir / ".env").write_text("SECRET=local-test\n", encoding="utf-8")
        search_dir = run_dir / "search_data"
        search_dir.mkdir(parents=True, exist_ok=True)
        for idx in range(20):
            file_path = search_dir / f"record_{idx}.py"
            file_path.write_text("import os\nprint('match')\n", encoding="utf-8")

    def _run_repo_tree_test(self, backend_root: Path) -> tuple[str, bool, str]:
        data = self._call_tool(
            workspace_id=uuid.uuid4().hex,
            run_id=uuid.uuid4().hex,
            tool_name="repo_tree",
            args={"root": ".", "absolute_root": str(backend_root.resolve()), "max_depth": 3},
        )
        if not data.get("ok"):
            return ("repo_tree", False, f"repo_tree failed: {data.get('error')}")
        stats = data["result"]["stats"]
        if stats.get("entries", 0) <= 0:
            return ("repo_tree", False, "stats.entries is zero")
        if not stats.get("excluded_patterns"):
            return ("repo_tree", False, "excluded patterns missing from stats")
        blacklisted = [
            entry
            for entry in data["result"]["entries"]
            if entry["path"].startswith(".git") or entry["path"].startswith(".venv")
        ]
        if blacklisted:
            return ("repo_tree", False, "excluded directories leaked into entries")
        return ("repo_tree", True, "absolute repo_tree succeeded with exclusions")

    def _run_search_code_test(self, run_dir: Path, workspace_id: str, run_id: str) -> tuple[str, bool, str]:
        args = {
            "query": "import",
            "is_regex": False,
            "case_sensitive": False,
            "root": "search_data",
            "include_globs": ["**/*.py"],
            "max_results": 10,
        }
        data = self._call_tool(workspace_id, run_id, "search_code", args)
        if not data.get("ok"):
            return ("search_code", False, f"search_code failed: {data.get('error')}")
        stats = data["result"]["stats"]
        if stats.get("files_scanned", 0) <= 0:
            return ("search_code", False, "no files scanned")
        if not data["result"].get("truncated"):
            return ("search_code", False, "max_results did not trigger truncation")
        meta = data.get("meta") or {}
        policy_meta = meta.get("policy")
        if not policy_meta:
            return ("search_code", False, "meta.policy missing from search_code response")
        return ("search_code", True, "search_code trimmed with policy metadata")

    def _run_file_read_tests(self, run_dir: Path, workspace_id: str, run_id: str) -> List[tuple[str, bool, str]]:
        results: List[tuple[str, bool, str]] = []
        data = self._call_tool(workspace_id, run_id, "file_read", {"path": "manage.py"})
        if not data.get("ok"):
            results.append(("file_read_manage", False, f"file_read/manage.py failed: {data.get('error')}"))
        else:
            meta = data.get("meta") or {}
            if not meta.get("policy"):
                results.append(("file_read_manage", False, "meta.policy missing for manage.py read"))
            else:
                results.append(("file_read_manage", True, "manage.py read OK with policy metadata"))

        blocked = self._call_tool(workspace_id, run_id, "file_read", {"path": ".env"})
        if blocked.get("ok"):
            results.append(("file_read_env", False, "file_read .env unexpectedly succeeded"))
        else:
            code = blocked.get("error", {}).get("code", "")
            meta = blocked.get("meta", {})
            if code != "tool_runner.PATH_EXCLUDED":
                results.append(("file_read_env", False, f"unexpected error code: {code}"))
            elif not meta.get("policy"):
                results.append(("file_read_env", False, "meta.policy missing for .env rejection"))
            else:
                results.append(("file_read_env", True, "file_read .env blocked with policy metadata"))
        return results

    def _run_file_write_patch_tests(
        self,
        run_dir: Path,
        workspace_id: str,
        run_id: str,
        escape_workspace_id: str,
        escape_run_id: str,
        escape_dir: Path,
    ) -> List[tuple[str, bool, str]]:
        results: List[tuple[str, bool, str]] = []
        write_resp = self._call_tool(
            workspace_id,
            run_id,
            "file_write",
            {"path": "smoke/hello.txt", "content": "hello\n", "mode": "text", "make_dirs": True},
        )
        if not write_resp.get("ok"):
            results.append(("file_write_sandbox", False, f"sandbox write failed: {write_resp.get('error')}"))
        else:
            results.append(("file_write_sandbox", True, "sandbox write succeeded"))
        patch_text = """--- a/smoke/hello.txt
+++ b/smoke/hello.txt
@@ -1 +1 @@
-hello
+hello world
"""
        patch_resp = self._call_tool(
            workspace_id,
            run_id,
            "file_patch",
            {"path": "smoke/hello.txt", "patch_unified": patch_text},
        )
        if not patch_resp.get("ok"):
            results.append(("file_patch_sandbox", False, f"patch failed: {patch_resp.get('error')}"))
        else:
            results.append(("file_patch_sandbox", True, "file_patch succeeded in sandbox"))
        blocked_resp = self._call_tool(
            escape_workspace_id,
            escape_run_id,
            "file_write",
            {"path": "outside.txt", "content": "blocked\n", "mode": "text"},
        )
        code = blocked_resp.get("error", {}).get("code")
        if blocked_resp.get("ok") or code != "tool_runner.WRITE_NOT_PERMITTED":
            results.append(("file_write_repo_blocked", False, f"repo path write did not produce WRITE_NOT_PERMITTED ({code})"))
        else:
            results.append(("file_write_repo_blocked", True, "repo path write blocked by guardrail"))
        return results

    def _run_shell_and_python_tests(self, run_dir: Path, workspace_id: str, run_id: str) -> List[tuple[str, bool, str]]:
        results: List[tuple[str, bool, str]] = []
        shell_limit = 128
        shell_resp = self._call_tool(
            workspace_id,
            run_id,
            "shell_exec",
            {
                "cmd": ["powershell", "-NoProfile", "-Command", "1..5 | ForEach-Object { Write-Output \"line $_\" }"],
                "cwd": ".",
                "env": {},
            },
            limits={"timeout_s": 10, "max_output_bytes": shell_limit},
        )
        if not shell_resp.get("ok"):
            results.append(("shell_exec_small", False, f"shell_exec failed: {shell_resp.get('error')}"))
        else:
            stdout = shell_resp.get("stdout", "")
            if not stdout:
                results.append(("shell_exec_small", False, "shell_exec returned empty stdout"))
            else:
                results.append(("shell_exec_small", True, "shell_exec captured output"))
        shell_trunc = self._call_tool(
            workspace_id,
            run_id,
            "shell_exec",
            {
                "cmd": ["powershell", "-NoProfile", "-Command", "1..100 | ForEach-Object { Write-Output \"line $_\" }"],
                "cwd": ".",
                "env": {},
            },
            limits={"timeout_s": 10, "max_output_bytes": 64},
        )
        stdout = shell_trunc.get("stdout", "")
        if len(stdout) >= 64:
            results.append(("shell_exec_truncation", True, "shell_exec output truncated by limit"))
        else:
            results.append(("shell_exec_truncation", False, "shell_exec output shorter than limit"))
        python_resp = self._call_tool(
            workspace_id,
            run_id,
            "python_exec",
            {"code": "print('hi from python')"},
            limits={"timeout_s": 5, "max_output_bytes": 128},
        )
        if not python_resp.get("ok"):
            results.append(("python_exec_simple", False, f"python_exec failed: {python_resp.get('error')}"))
        else:
            stdout = python_resp.get("stdout", "")
            if "hi from python" not in stdout:
                results.append(("python_exec_simple", False, "python_exec stdout missing expected text"))
            else:
                results.append(("python_exec_simple", True, "python_exec captured stdout"))
        python_trunc_resp = self._call_tool(
            workspace_id,
            run_id,
            "python_exec",
            {"code": "for i in range(100): print('line', i)"},
            limits={"timeout_s": 5, "max_output_bytes": 80},
        )
        trunc_stdout = python_trunc_resp.get("stdout", "")
        if len(trunc_stdout) >= 80:
            results.append(("python_exec_truncation", True, "python_exec output truncated to limit"))
        else:
            results.append(("python_exec_truncation", False, "python_exec output shorter than limit"))
        return results
