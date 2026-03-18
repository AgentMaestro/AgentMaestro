import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from toolrunner.app.models import RunTestsArgs
import toolrunner.app.tools.run_tests as run_tests_module
from toolrunner.app.tools.run_tests import run_predefined_tests
from toolrunner.app.tools.subprocess_utils import SubprocessExecutionResult


def _payload(response):
    return json.loads(response.body)


def _policy(root: Path) -> dict[str, object]:
    return {"repo_root": str(root), "allowed_roots": [str(root)]}


def test_run_tests_runs_backend_suite(tmp_path: Path, monkeypatch):
    script = tmp_path / "backend" / "scripts" / "runtests.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("Write-Output 'backend ok'\n", encoding="utf-8")
    captured = {}

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        captured["command"] = list(command)
        captured["cwd"] = str(cwd)
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=0,
            stdout="backend ok\n",
            stderr="",
            duration_ms=15,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_tests_module, "run_subprocess", fake_run_subprocess)
    response = run_predefined_tests(
        tmp_path,
        RunTestsArgs(suites=["backend"], timeout_seconds=120),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert payload["ok"]
    assert payload["results"][0]["suite"] == "backend"
    assert captured["command"][:6] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def test_run_tests_all_runs_sequentially(tmp_path: Path, monkeypatch):
    backend_script = tmp_path / "backend" / "scripts" / "runtests.ps1"
    toolrunner_script = tmp_path / "toolrunner" / "scripts" / "runtests.ps1"
    backend_script.parent.mkdir(parents=True)
    toolrunner_script.parent.mkdir(parents=True)
    backend_script.write_text("Write-Output 'backend'\n", encoding="utf-8")
    toolrunner_script.write_text("Write-Output 'toolrunner'\n", encoding="utf-8")
    seen = []

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        seen.append(command[5])
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=0,
            stdout="ok\n",
            stderr="",
            duration_ms=10,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_tests_module, "run_subprocess", fake_run_subprocess)
    response = run_predefined_tests(tmp_path, RunTestsArgs(suites=["all"]), _policy(tmp_path))
    payload = _payload(response)
    assert payload["ok"]
    assert [item["suite"] for item in payload["results"]] == ["backend", "toolrunner"]
    assert seen == [str(backend_script), str(toolrunner_script)]


def test_run_tests_falls_back_to_test_ps1(tmp_path: Path, monkeypatch):
    fallback = tmp_path / "backend" / "scripts" / "test.ps1"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("Write-Output 'fallback'\n", encoding="utf-8")

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=0,
            stdout="fallback\n",
            stderr="",
            duration_ms=8,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_tests_module, "run_subprocess", fake_run_subprocess)
    response = run_predefined_tests(tmp_path, RunTestsArgs(suites=["backend"]), _policy(tmp_path))
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["fallback_scripts"] == ["backend/scripts/test.ps1"]


def test_run_tests_rejects_invalid_suite_value():
    with pytest.raises(ValidationError):
        RunTestsArgs(suites=["invalid"])


def test_run_tests_returns_structured_failure(tmp_path: Path, monkeypatch):
    script = tmp_path / "toolrunner" / "scripts" / "runtests.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("Write-Output 'toolrunner fail'\n", encoding="utf-8")

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=1,
            stdout="failed\n",
            stderr="boom\n",
            duration_ms=22,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_tests_module, "run_subprocess", fake_run_subprocess)
    response = run_predefined_tests(tmp_path, RunTestsArgs(suites=["toolrunner"]), _policy(tmp_path))
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["results"][0]["exit_code"] == 1
    assert payload["results"][0]["ok"] is False

