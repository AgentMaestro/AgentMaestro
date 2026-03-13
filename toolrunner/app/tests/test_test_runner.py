import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE
from toolrunner.app.models import RunnerTestArgs
from toolrunner.app.tools import test_runner as test_runner_module
from toolrunner.app.tools.test_runner import run_tests


def _stdout_with_failure() -> str:
    return """============================= test session starts =============================
platform win32 -- Python 3.12.10
======== 1 failed, 2 passed in 0.01s ========
FAILED app/tests/test_sample.py::test_failure - assert False
_____________________ app/tests/test_sample.py::test_failure ____________________
Traceback (most recent call last):
  File "app/tests/test_sample.py", line 10, in test_failure
    assert False
AssertionError: assert False
"""


def test_test_runner_missing_script(tmp_path: Path):
    args = RunnerTestArgs(kind="powershell_script", script_path="missing.ps1")
    response = run_tests(tmp_path, args)
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")


def test_test_runner_powershell_invokes_ps(tmp_path: Path, monkeypatch):
    script = tmp_path / "script.ps1"
    script.write_text("Write-Output 'ok'")
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(status_code=200, content={"ok": True, "result": {"exit_code": 0, "duration_ms": 0, "timed_out": False, "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}})

    monkeypatch.setattr(test_runner_module, "run_command", fake_run_command)
    args = RunnerTestArgs(kind="powershell_script", script_path=script.name, script_args=["-q"])
    resp = run_tests(tmp_path, args)
    assert resp.status_code == 200
    assert captured["cmd"][:6] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def test_test_runner_repo_relative_script_uses_policy_repo_root(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    script = repo_root / "toolrunner" / "scripts" / "runtests.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("Write-Output 'ok'")
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(status_code=200, content={"ok": True, "result": {"exit_code": 0, "duration_ms": 0, "timed_out": False, "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}})

    monkeypatch.setattr(test_runner_module, "run_command", fake_run_command)
    args = RunnerTestArgs(kind="powershell_script", script_path="toolrunner/scripts/runtests.ps1", script_args=["-q"])
    resp = run_tests(tmp_path, args, policy={"repo_root": str(repo_root), "allowed_roots": [str(repo_root)]})
    assert resp.status_code == 200
    assert captured["cmd"][:6] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def test_test_runner_pytest_summary(monkeypatch, tmp_path: Path):
    captured_env: dict[str, str | None] = {}

    def fake_run_command(run_dir, run_args):
        captured_env["cmd"] = run_args.cmd
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": _stdout_with_failure(),
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(test_runner_module, "run_command", fake_run_command)
    args = RunnerTestArgs(kind="pytest", pytest_args=["app/tests/test_sample.py::test_failure"])
    response = run_tests(tmp_path, args)
    payload = json.loads(response.body)["result"]
    assert payload["summary"]["failed"] == 1
    assert payload["parse_mode"] == "pytest"
    assert captured_env["cmd"][:3] == [PYTHON_INTERPRETER, "-m", "pytest"]
    assert payload["python_interpreter"] == PYTHON_INTERPRETER
    assert payload["python_interpreter_source"] == PYTHON_INTERPRETER_SOURCE


def test_test_runner_command_kind(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str]] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 0,
                    "timed_out": False,
                    "stdout": "done\n",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(test_runner_module, "run_command", fake_run_command)
    args = RunnerTestArgs(kind="command", cmd=["echo", "hello"])
    response = run_tests(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    assert captured["cmd"] == ["echo", "hello"]


def test_test_runner_missing_pytest_module(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "",
                    "stderr": "ModuleNotFoundError: No module named 'pytest'",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(test_runner_module, "run_command", fake_run_command)
    response = run_tests(tmp_path, RunnerTestArgs(kind="pytest", pytest_args=["tests/test_sample.py"]))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("MISSING_RUNTIME_DEPENDENCY")
    assert payload["error"]["details"]["python_interpreter"] == PYTHON_INTERPRETER
    assert payload["error"]["details"]["python_interpreter_source"] == PYTHON_INTERPRETER_SOURCE
