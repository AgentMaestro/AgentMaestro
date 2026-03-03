"""Integration exercises for the ToolRunner bridge."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from django.conf import settings

from llm.services.toolrunner_bridge import run_tool


RUNNER_SHA_STRING = "1fef5c2db294f4f96b467d5a7b976baca5399942f1a2a5c4686d3a611ab10470"


def _bridge_tests_enabled() -> bool:
    value = os.environ.get("RUN_TOOLRUNNER_BRIDGE_TESTS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


pytestmark = pytest.mark.skipif(
    not _bridge_tests_enabled(),
    reason="ToolRunner bridge tests require RUN_TOOLRUNNER_BRIDGE_TESTS=1",
)


BACKEND_ROOT = Path(settings.BASE_DIR).resolve()


def _tool_result_payload(response: dict) -> dict:
    assert response.get("ok"), "bridge response did not succeed"
    payload = response.get("result", {})
    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    result_payload = payload.get("result")
    assert isinstance(result_payload, dict)
    return result_payload


def _call_tool(tool: str, args: dict) -> dict:
    return asyncio.run(run_tool(tool, args))


def test_repository_orientation_references_real_folders():
    response = _call_tool(
        "repo_tree",
        {
            "absolute_root": str(BACKEND_ROOT),
            "max_depth": 2,
            "include_files": True,
            "include_dirs": True,
        },
    )
    payload = _tool_result_payload(response)
    entries = payload.get("entries", [])
    assert entries, "repo_tree should return entries"
    top_level = {
        entry["path"]
        for entry in entries
        if entry.get("path") and entry.get("depth", 0) == 0
    }
    assert any("agentmaestro" in path.lower() for path in top_level)
    summary = "Top level entries: " + ", ".join(sorted(top_level)[:5])
    assert ", " in summary


def test_targeted_code_lookup_reads_settings():
    response = _call_tool(
        "search_code",
        {
            "absolute_root": str(BACKEND_ROOT),
            "query": "TOOLRUNNER_BASE_URL",
            "include_globs": ["**/*.py"],
            "max_results": 5,
        },
    )
    payload = _tool_result_payload(response)
    matches = payload.get("matches") or []
    assert matches, "search_code should find the setting key"
    target_file = matches[0]["path"]
    read_response = _call_tool(
        "file_read",
        {
            "path": target_file,
            "absolute_root": str(BACKEND_ROOT),
            "mode": "text",
            "encoding": "utf-8",
            "max_bytes": 4096,
        },
    )
    read_payload = _tool_result_payload(read_response)
    content = read_payload.get("content", "")
    assert "TOOLRUNNER_BASE_URL" in content or "AGENTMAESTRO_TOOLRUNNER_URL" in content
    assert target_file in read_payload.get("path", target_file)


def test_patch_inside_sandbox_and_read_back():
    target_path = "bridge/llm_payload.json"
    initial_content = '{"value":1,"status":"new"}\n'
    write_response = _call_tool(
        "file_write",
        {
            "path": target_path,
            "content": initial_content,
            "mode": "text",
            "overwrite": True,
        },
    )
    assert write_response.get("ok"), "file_write should succeed"
    patch_text = """--- a/bridge/llm_payload.json
+++ b/bridge/llm_payload.json
@@ -1 +1 @@
-{"value":1,"status":"new"}
+{"value":2,"status":"patched"}
"""
    patch_response = _call_tool(
        "file_patch",
        {
            "path": target_path,
            "patch_unified": patch_text,
        },
    )
    assert patch_response.get("ok"), "file_patch should succeed"
    read_response = _call_tool(
        "file_read",
        {
            "path": target_path,
            "mode": "text",
            "encoding": "utf-8",
            "max_bytes": 4096,
        },
    )
    read_payload = _tool_result_payload(read_response)
    assert '"value":2' in read_payload.get("content", "")


def test_shell_exec_lists_sandbox_contents():
    response = _call_tool(
        "shell_exec",
        {
            "cmd": [
                "python",
                "-c",
                "import json,os; print(json.dumps(sorted(os.listdir('.'))))",
            ],
            "cwd": ".",
        },
    )
    stdout = response.get("meta", {}).get("stdout", "").strip()
    assert stdout, "shell_exec should emit output"
    data = json.loads(stdout)
    assert isinstance(data, list)
    assert data


def test_python_exec_computes_expected_sha():
    response = _call_tool(
        "python_exec",
        {"code": "import hashlib\nprint(hashlib.sha256(b'agentmaestro').hexdigest())"},
    )
    stdout = response.get("meta", {}).get("stdout", "").strip()
    assert stdout == RUNNER_SHA_STRING


