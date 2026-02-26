import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitApplyArgs
from toolrunner.app.tools import git_apply as git_apply_module
from toolrunner.app.tools.git_apply import run_git_apply


def _response(result):
    return JSONResponse(status_code=200, content={"ok": True, "result": result})


def test_git_apply_success(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        commands.append(run_args.cmd)
        return _response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "applied",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_apply_module, "run_command", fake_run_command)
    args = GitApplyArgs(patch_unified="diff", strip_prefix=2)
    response = run_git_apply(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    assert commands[0][:3] == ["git", "apply", "-p2"]
    assert "--reject" in commands[0]
    result = payload["result"]
    assert result["applied"]
    assert result["rejects_created"] is False
    assert result["reject_paths"] == []
    assert result["repo_dir"] == "."
    assert result["strip_prefix"] == 2


def test_git_apply_check_mode(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        commands.append(run_args.cmd)
        return _response(
            {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "check",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_apply_module, "run_command", fake_run_command)
    args = GitApplyArgs(patch_unified="diff", check=True, reject=False)
    response = run_git_apply(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["ok"]
    result = payload["result"]
    assert "--check" in commands[0]
    assert result["applied"] is False
    assert result["check_passed"]
    assert result["rejects_created"] is False
    assert result["reject_paths"] == []


def test_git_apply_reject_created(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        (run_dir / "patch.rej").write_text("reject")
        return _response(
            {
                "exit_code": 1,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "",
                "stderr": "reject",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_apply_module, "run_command", fake_run_command)
    args = GitApplyArgs(patch_unified="diff", reject=True)
    response = run_git_apply(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    result = payload["result"]
    assert payload["ok"]
    assert result["rejects_created"] is True
    assert result["reject_paths"] == ["patch.rej"]


def test_git_apply_reject_without_files(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return _response(
            {
                "exit_code": 1,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": "",
                "stderr": "reject",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        )

    monkeypatch.setattr(git_apply_module, "run_command", fake_run_command)
    args = GitApplyArgs(patch_unified="diff", reject=True)
    response = run_git_apply(tmp_path, args)
    payload = json.loads(response.body.decode("utf-8"))
    result = payload["result"]
    assert payload["ok"]
    assert result["rejects_created"] is False
    assert result["reject_paths"] == []


def test_git_apply_path_escape(tmp_path: Path):
    response = run_git_apply(tmp_path, GitApplyArgs(repo_dir="../outside", patch_unified="diff"))
    payload = json.loads(response.body.decode("utf-8"))
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
