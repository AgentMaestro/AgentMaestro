import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import LintArgs
from toolrunner.app.tools import lint_runner as lint_module
from toolrunner.app.tools.lint_runner import run_linters


def _fake_ruff_output():
    return json.dumps(
        [
            {
                "code": "F401",
                "message": "Imported but unused",
                "path": "app/models.py",
                "row": 24,
                "column": 1,
            }
        ]
    )


def test_lint_runner_ruff_parses_json(monkeypatch, tmp_path: Path):
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
                    "stdout": _fake_ruff_output(),
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="ruff", paths=["app"])
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    result = payload["result"]
    assert result["parse_mode"] == "ruff"
    assert result["issues"][0]["code"] == "F401"
    assert result["issues"][0]["severity"] == "error"
    assert result["parse_source"] == "stdout"
    assert result["parse_warning"] is None
    cmd = captured["cmd"]
    assert cmd == [
        "python",
        "-m",
        "ruff",
        "check",
        "--output-format=json",
        str(tmp_path / "app"),
    ]


def test_lint_runner_command_mode(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="command", cmd=["echo", "hello"])
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    result = payload["result"]
    assert result["issues"] == []
    assert result["parse_mode"] == "none"
    assert captured["cmd"] == ["echo", "hello"]


def test_lint_runner_parse_truncated(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "{}",  # truncated/dropped
                    "stderr": "",
                    "stdout_truncated": True,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="ruff")
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["issues"] == []
    assert result["parse_warning"] == "ruff output truncated; issues not parsed"
    assert result["parse_source"] == "stdout"


def test_lint_runner_parse_from_stderr(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "not-json",
                    "stderr": _fake_ruff_output(),
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="ruff")
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["parse_source"] == "stderr"
    assert result["parse_warning"] is None


def test_lint_runner_output_format_idempotent(monkeypatch, tmp_path: Path):
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
                    "stdout": _fake_ruff_output(),
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="ruff", args=["check", "--output-format=json"])
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    assert payload["result"]["parse_source"] == "stdout"
    assert captured["cmd"].count("--output-format=json") == 1


def test_lint_runner_parse_invalid_json(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 1,
                    "duration_ms": 1,
                    "timed_out": False,
                    "stdout": "not-json",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(lint_module, "run_command", fake_run_command)
    args = LintArgs(tool="ruff")
    response = run_linters(tmp_path, args)
    payload = json.loads(response.body)
    result = payload["result"]
    assert result["parse_source"] == "stdout"
    assert result["issues"] == []
    assert result["parse_warning"] == "ruff output is not valid JSON"


def test_lint_runner_path_escape(monkeypatch, tmp_path: Path):
    response = run_linters(tmp_path, LintArgs(tool="ruff", paths=["../outside"]))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
