import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitBranchCreateArgs
from toolrunner.app.tools import git_branch_create as branch_module
from toolrunner.app.tools.git_branch_create import run_git_branch_create


def _response(result):
    return JSONResponse(status_code=200, content={"ok": True, "result": result})


def test_git_branch_create(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(branch_module, "run_command", fake_run_command)
    args = GitBranchCreateArgs(name="agent/branch", checkout=True, force=True)
    response = run_git_branch_create(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert commands[0][:3] == ["git", "branch", "-f"]
    assert commands[0][-1] == "HEAD"
    assert commands[1][:4] == ["git", "switch", "--", "agent/branch"]
    result = payload["result"]
    assert result["checked_out"]
    assert result["repo_dir"] == "."


def test_git_branch_create_no_checkout(monkeypatch, tmp_path: Path):
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

    monkeypatch.setattr(branch_module, "run_command", fake_run_command)
    args = GitBranchCreateArgs(name="test", checkout=False)
    response = run_git_branch_create(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert len(commands) == 1
    assert payload["result"]["checked_out"] is False


def test_git_branch_create_timeout_in_branch_phase(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return _response(
            {
                "exit_code": None,
                "duration_ms": 15000,
                "timed_out": True,
                "stdout": "",
                "stderr": "command timed out",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "timeout_ms": 15000,
                "timeout_source": "args.timeout_ms",
            }
        )

    monkeypatch.setattr(branch_module, "run_command", fake_run_command)
    response = run_git_branch_create(tmp_path, GitBranchCreateArgs(name="agent/branch", checkout=False))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("TIMED_OUT")
    assert payload["error"]["details"] == {
        "phase": "branch",
        "timed_out": True,
        "timeout_ms": 15000,
        "timeout_source": "args.timeout_ms",
    }



def test_git_branch_create_timeout_in_checkout_phase(monkeypatch, tmp_path: Path):
    calls = {"count": 0}

    def fake_run_command(run_dir, run_args):
        calls["count"] += 1
        if calls["count"] == 1:
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
        return _response(
            {
                "exit_code": None,
                "duration_ms": 15000,
                "timed_out": True,
                "stdout": "",
                "stderr": "command timed out",
                "stdout_truncated": False,
                "stderr_truncated": False,
                "timeout_ms": 15000,
                "timeout_source": "args.timeout_ms",
            }
        )

    monkeypatch.setattr(branch_module, "run_command", fake_run_command)
    response = run_git_branch_create(tmp_path, GitBranchCreateArgs(name="agent/branch", checkout=True))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("TIMED_OUT")
    assert payload["error"]["details"] == {
        "phase": "checkout",
        "timed_out": True,
        "timeout_ms": 15000,
        "timeout_source": "args.timeout_ms",
    }


def test_git_branch_create_path_escape(tmp_path: Path):
    response = run_git_branch_create(tmp_path, GitBranchCreateArgs(repo_dir="../outside", name="x"))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
