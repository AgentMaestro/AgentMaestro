import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitPushArgs
from toolrunner.app.tools import git_push as git_push_module
from toolrunner.app.tools.git_push import run_git_push


def _response(result):
    return JSONResponse(status_code=200, content={"ok": True, "result": result})


def test_git_push_defaults(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(git_push_module, "run_command", fake_run_command)
    args = GitPushArgs(ref="feature/test")
    response = run_git_push(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert commands[0] == ["git", "push", "-u", "origin", "--", "feature/test"]
    result = payload["result"]
    assert result["repo_dir"] == "."
    assert result["remote"] == "origin"
    assert result["ref"] == "feature/test"
    assert result["pushed"]
    assert result["raw"]["stdout"] == "ok"


def test_git_push_force_without_upstream(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(git_push_module, "run_command", fake_run_command)
    args = GitPushArgs(ref="feature/force", set_upstream=False, force=True, remote="upstream")
    response = run_git_push(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert commands[0] == ["git", "push", "upstream", "--", "feature/force", "--force-with-lease"]
    assert payload["result"]["raw"]["stdout"] == "ok"


def test_git_push_path_escape(tmp_path: Path):
    response = run_git_push(tmp_path, GitPushArgs(repo_dir="../outside", ref="feature"))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
