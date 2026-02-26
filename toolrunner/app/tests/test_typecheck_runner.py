import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import TypecheckArgs
from toolrunner.app.tools import typecheck_runner as typecheck_module
from toolrunner.app.tools.typecheck_runner import run_typecheck


def _fake_pyright_output():
    return json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": "app/services/foo.py",
                    "message": "Argument of type 'str' is not assignable to parameter of type 'int'",
                    "rule": "reportGeneralTypeIssues",
                    "severity": "error",
                    "range": {"start": {"line": 88, "character": 12}},
                }
            ]
        }
    )


def test_typecheck_runner_pyright(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": _fake_pyright_output(),
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(typecheck_module, "run_command", fake_run_command)
    args = TypecheckArgs(tool="pyright")
    response = run_typecheck(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    result = payload["result"]
    assert result["parse_mode"] == "pyright"
    diag = result["diagnostics"][0]
    assert diag["code"] == "reportGeneralTypeIssues"
    assert diag["line"] == 89
    assert diag["col"] == 13
    assert diag["severity"] == "error"
    assert captured["cmd"][:3] == ["python", "-m", "pyright"]


def test_typecheck_runner_command(monkeypatch, tmp_path: Path):
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
                    "stdout": "",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(typecheck_module, "run_command", fake_run_command)
    args = TypecheckArgs(tool="command", cmd=["echo", "ok"])
    response = run_typecheck(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    assert payload["result"]["parse_mode"] == "none"
    assert captured["cmd"] == ["echo", "ok"]


def test_typecheck_runner_pyright_stderr(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "invalid-json",
                    "stderr": _fake_pyright_output(),
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(typecheck_module, "run_command", fake_run_command)
    args = TypecheckArgs(tool="pyright")
    response = run_typecheck(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["parse_mode"] == "pyright"
    assert result["parse_source"] == "stderr"
    assert result["parse_warning"] is None


def test_typecheck_runner_pyright_invalid(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "not json",
                    "stderr": "still not json",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(typecheck_module, "run_command", fake_run_command)
    args = TypecheckArgs(tool="pyright")
    response = run_typecheck(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["diagnostics"] == []
    assert result["parse_warning"] == "pyright output is not valid JSON"
    assert result["parse_source"] == "stdout"


def test_typecheck_runner_mypy(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "app/models.py:10:5: error: something went wrong [code]",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(typecheck_module, "run_command", fake_run_command)
    args = TypecheckArgs(tool="mypy")
    response = run_typecheck(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    diag = result["diagnostics"][0]
    assert diag["path"] == "app/models.py"
    assert diag["line"] == 10
    assert diag["col"] == 5
    assert diag["code"] == "code"
