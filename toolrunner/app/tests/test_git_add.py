import json
from pathlib import Path

from fastapi.responses import JSONResponse
import pytest

from toolrunner.app.models import GitAddArgs
from toolrunner.app.tools import git_add as git_add_module
from toolrunner.app.tools.git_add import run_git_add


def _response(result):
    return JSONResponse(status_code=200, content={"ok": True, "result": result})


def test_git_add_paths(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        commands.append(run_args.cmd)
        return _response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "ok",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_add_module, "run_command", fake_run_command)
    args = GitAddArgs(paths=["toolrunner/app/file_patch.py", "toolrunner/app/file_read.py"])
    response = run_git_add(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert commands[0][:2] == ["git", "add"]
    assert "--" in commands[0]
    assert payload["result"]["staged_paths"] == ["toolrunner/app/file_patch.py", "toolrunner/app/file_read.py"]
    assert payload["result"]["raw"]["stdout"] == "ok"


def test_git_add_all(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return _response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "ok",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_add_module, "run_command", fake_run_command)
    args = GitAddArgs(all=True)
    response = run_git_add(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert payload["result"]["staged_paths"] == []
    assert payload["result"]["raw"]["stdout"] == "ok"


def test_git_add_intent_to_add(monkeypatch, tmp_path: Path):
    captured: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        captured.append(run_args.cmd)
        return _response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "ok",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_add_module, "run_command", fake_run_command)
    args = GitAddArgs(intent_to_add=True, paths=["toolrunner/app/file_patch.py"])
    response = run_git_add(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert "-N" in captured[0]


def test_git_add_path_escape(tmp_path: Path):
    response = run_git_add(tmp_path, GitAddArgs(paths=["../outside"]))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")


def test_git_add_invalid_all_with_paths():
    with pytest.raises(Exception) as excinfo:
        GitAddArgs(all=True, paths=["toolrunner/app/file_patch.py"])
    assert "all=True cannot be combined" in str(excinfo.value)


def test_git_add_intent_requires_paths():
    with pytest.raises(Exception) as excinfo:
        GitAddArgs(intent_to_add=True)
