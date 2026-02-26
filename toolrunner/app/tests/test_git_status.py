import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitStatusArgs, RunCommandArgs
from toolrunner.app.tools import git_status as git_status_module
from toolrunner.app.tools.git_status import run_git_status


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _fake_success_response(stdout: str = "", stderr: str = ""):
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "result": {
                "exit_code": 0,
                "duration_ms": 1,
                "timed_out": False,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": False,
                "stderr_truncated": False,
            },
        },
    )


def _fake_error_response(message: str, code: str = "NOT_FOUND"):
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "error": {
                "code": f"tool_runner.{code}",
                "message": message,
                "details": {},
            },
        },
    )


def test_git_status_parses_branches_and_paths(monkeypatch, tmp_path: Path):
    sample_output = """# branch.oid abcdef123
# branch.head feature
# branch.upstream origin/feature
# branch.ab +2 -1
1 M. N... 100644 100644 100644 abc abc staged.txt
1 M. N... 100644 100644 100644 def def multi   spaced file.txt
1 .M N... 100644 100644 100644 ghi ghi unstaged.txt
2 M. N... 100644 100644 100644 jkl jkl R100 renamed   path\told path
u UU N... 100644 100644 100644 mno mno conflict.txt
? new file.txt
"""
    captured: dict[str, object] = {}

    def fake_run_command(run_dir, run_args):
        captured["run_dir"] = run_dir
        captured["cmd"] = run_args.cmd
        captured["cwd"] = run_args.cwd
        captured["timeout_ms"] = run_args.timeout_ms
        captured["max_output_bytes"] = run_args.max_output_bytes
        return _fake_success_response(stdout=sample_output)

    monkeypatch.setattr(git_status_module, "run_command", fake_run_command)
    args = GitStatusArgs()
    response = run_git_status(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    result = payload["result"]
    branch = result["branch"]
    assert branch["name"] == "feature"
    assert branch["upstream"] == "origin/feature"
    assert branch["ahead"] == 2
    assert branch["behind"] == 1
    assert branch["head_oid"] == "abcdef123"
    assert branch["detached"] is False
    assert "staged.txt" in result["staged"]
    assert "multi   spaced file.txt" in result["staged"]
    assert "renamed   path" in result["staged"]
    assert result["unstaged"] == ["unstaged.txt"]
    assert result["untracked"] == ["new file.txt"]
    assert result["conflicts"] == ["conflict.txt"]
    assert not result["is_clean"]
    assert result["raw"]["stdout"] == sample_output.replace("\r\n", "\n")
    assert result["raw"]["stderr"] == ""
    assert captured["cmd"] == ["git", "status", "--porcelain=v2", "--branch"]
    assert captured["cwd"] == "."


def test_git_status_respects_include_untracked_flag(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return _fake_success_response()

    monkeypatch.setattr(git_status_module, "run_command", fake_run_command)
    args = GitStatusArgs(include_untracked=False)
    response = run_git_status(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    assert payload["result"]["untracked"] == []
    assert "--untracked-files=no" in captured["cmd"]


def test_git_status_not_git_repo(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return _fake_error_response("not a git repository", code="NOT_FOUND")

    monkeypatch.setattr(git_status_module, "run_command", fake_run_command)
    args = GitStatusArgs()
    response = run_git_status(tmp_path, args)
    payload = _payload(response)

    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")
