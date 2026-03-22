import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.config import PYTHON_INTERPRETER, PYTHON_INTERPRETER_SOURCE
from toolrunner.app.models import FormatArgs
from toolrunner.app.tools import format_runner as format_module
from toolrunner.app.tools.format_runner import run_formatter


def test_format_runner_ruff_check(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str] | None] = {}
    stdout = (
        "+++ app/models.py\n"
        "@@ -1,4 +1,4 @@\n"
        "--- /dev/null\n"
    )

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 2,
                    "timed_out": False,
                    "stdout": stdout,
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(format_module, "run_command", fake_run_command)
    args = FormatArgs(tool="ruff_format", mode="check", paths=["app"])
    response = run_formatter(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    result = payload["result"]
    assert result["changed_files"] == ["app/models.py"]
    assert captured["cmd"][0:5] == [PYTHON_INTERPRETER, "-I", "-m", "ruff", "format"]
    assert "--check" in captured["cmd"]
    assert "--diff" in captured["cmd"]
    assert captured["cmd"].count("format") == 1
    assert result["parse_mode"] == "ruff_format"
    assert result["parse_warning"] is None
    assert result["python_interpreter"] == PYTHON_INTERPRETER
    assert result["python_interpreter_source"] == PYTHON_INTERPRETER_SOURCE


def test_format_runner_apply(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "+++ toolrunner/app/tests/test_format_runner.py\n",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(format_module, "run_command", fake_run_command)
    args = FormatArgs(tool="ruff_format", mode="apply", paths=["toolrunner/app"])
    response = run_formatter(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert captured["cmd"][:4] == [PYTHON_INTERPRETER, "-I", "-m", "ruff"]
    assert result["changed_files"] == ["toolrunner/app/tests/test_format_runner.py"]
    assert captured["cmd"].count("format") == 1
    assert result["parse_mode"] == "ruff_format"
    assert result["parse_warning"] is None


def test_format_runner_truncated(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "+++ app/models.py",
                    "stderr": "",
                    "stdout_truncated": True,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(format_module, "run_command", fake_run_command)
    args = FormatArgs(tool="ruff_format")
    response = run_formatter(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["parse_warning"] == "stdout truncated; changed_files may be incomplete"


def test_format_runner_path_escape():
    response = run_formatter(Path("."), FormatArgs(tool="ruff_format", paths=["../outside"]))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")


def test_format_runner_missing_ruff_module(monkeypatch, tmp_path: Path):
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
                    "stderr": "ModuleNotFoundError: No module named 'ruff'",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(format_module, "run_command", fake_run_command)
    response = run_formatter(tmp_path, FormatArgs(tool="ruff_format"))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("MISSING_RUNTIME_DEPENDENCY")
    assert payload["error"]["details"]["python_interpreter"] == PYTHON_INTERPRETER
    assert payload["error"]["details"]["python_interpreter_source"] == PYTHON_INTERPRETER_SOURCE
