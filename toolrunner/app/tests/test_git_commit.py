import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitCommitArgs
from toolrunner.app.tools import git_commit as git_commit_module
from toolrunner.app.tools.git_commit import run_git_commit


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _response(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
):
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "exit_code": exit_code,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        },
    )


def test_git_commit_stages_paths_and_commits(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        commands.append(run_args.cmd)
        cmd = run_args.cmd
        if cmd[:3] == ["git", "add", "--"]:
            return _response()
        if cmd == ["git", "commit", "-m", "Fix it"]:
            return _response(stdout="[main 1234abc] Fix it\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _response(stdout="1234abc\n")
        if cmd[:2] == ["git", "diff-tree"]:
            return _response(stdout="toolrunner/app/file_patch.py\n")
        return _response()

    monkeypatch.setattr(git_commit_module, "run_command", fake_run_command)
    args = GitCommitArgs(message="Fix it", paths_to_add=["toolrunner/app/file_patch.py"])
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    assert payload["result"]["commit_oid"] == "1234abc"
    assert payload["result"]["summary"] == "Fix it"
    assert payload["result"]["changed_files"] == 1
    assert payload["result"]["repo_dir"] == "."
    assert commands[0] == ["git", "add", "--", "toolrunner/app/file_patch.py"]
    assert ["git", "commit", "-m", "Fix it"] in commands


def test_git_commit_add_all_signoff_amend(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        commands.append(run_args.cmd)
        cmd = run_args.cmd
        if cmd == ["git", "add", "-A"]:
            return _response()
        if cmd[:3] == ["git", "commit", "-m"]:
            return _response(stdout="[main 1234] updated\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _response(stdout="abcd\n")
        if cmd[:2] == ["git", "diff-tree"]:
            return _response(stdout="file1.py\nfile2.py\n")
        return _response()

    monkeypatch.setattr(git_commit_module, "run_command", fake_run_command)
    args = GitCommitArgs(
        message="Update",
        add_all=True,
        signoff=True,
        amend=True,
    )
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    assert payload["result"]["changed_files"] == 2
    commit_cmds = [cmd for cmd in commands if cmd and cmd[1] == "commit"]
    assert commit_cmds
    assert "--signoff" in commit_cmds[-1]
    assert "--amend" in commit_cmds[-1]
    assert ["git", "add", "-A"] in commands


def test_git_commit_nothing_to_commit(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        if run_args.cmd and run_args.cmd[1] == "commit":
            return _response(stdout="nothing to commit, working tree clean\n", exit_code=1)
        return _response()

    monkeypatch.setattr(git_commit_module, "run_command", fake_run_command)
    args = GitCommitArgs(message="Nothing")
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert not payload["ok"]
    assert payload["error"]["code"].endswith("CONFLICT")
    assert payload["error"]["message"] == "nothing to commit"


def test_git_commit_propagates_add_error(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "tool_runner.INVALID_ARGUMENT",
                    "message": "bad add",
                    "details": {},
                },
            },
        )

    monkeypatch.setattr(git_commit_module, "run_command", fake_run_command)
    args = GitCommitArgs(message="fail", paths_to_add=["file"])
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert not payload["ok"]
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")


def test_git_commit_paths_escape(monkeypatch, tmp_path: Path):
    monster_called = False

    def fake_run_command(run_dir, run_args):
        nonlocal monster_called
        monster_called = True
        return _response()

    monkeypatch.setattr(git_commit_module, "run_command", fake_run_command)
    args = GitCommitArgs(message="Escape", paths_to_add=["../outside/file"])
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")
    assert not monster_called


def test_git_commit_paths_and_add_all_invalid(tmp_path: Path):
    args = GitCommitArgs(message="Conflict", paths_to_add=["file"], add_all=True)
    response = run_git_commit(tmp_path, args)
    payload = _payload(response)

    assert not payload["ok"]
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")
