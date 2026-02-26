import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitDiffArgs
from toolrunner.app.tools import git_diff as git_diff_module
from toolrunner.app.tools.git_diff import run_git_diff


def _fake_response(command_result: dict):
    return JSONResponse(status_code=200, content={"ok": True, "result": command_result})


def test_git_diff_basic(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return _fake_response(
            {
                "exit_code": 0,
                "duration_ms": 5,
                "timed_out": False,
                "stdout": "diff --git a/b c/d\r\n",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_diff_module, "run_command", fake_run_command)
    args = GitDiffArgs(paths=["toolrunner/app/file_patch.py"], context_lines=5, detect_renames=True)
    response = run_git_diff(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    result = payload["result"]
    assert result["repo_dir"] == "."
    assert result["paths"] == ["toolrunner/app/file_patch.py"]
    assert result["diff"].endswith("\n")
    assert "--find-renames" in captured["cmd"]
    assert "-U" in captured["cmd"]
    assert captured["cmd"][-1] == "toolrunner/app/file_patch.py"


def test_git_diff_staged(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str] | None] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return _fake_response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "staged diff",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_diff_module, "run_command", fake_run_command)
    args = GitDiffArgs(staged=True)
    response = run_git_diff(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    result = payload["result"]
    assert result["staged"]
    assert "--cached" in captured["cmd"]


def test_git_diff_truncated(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return _fake_response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "diff",
                "stderr": "",
                "stdout_truncated": True,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_diff_module, "run_command", fake_run_command)
    response = run_git_diff(tmp_path, GitDiffArgs())
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["result"]["truncated"]


def test_git_diff_path_escape(tmp_path: Path):
    response = run_git_diff(tmp_path, GitDiffArgs(paths=["../outside"]))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
