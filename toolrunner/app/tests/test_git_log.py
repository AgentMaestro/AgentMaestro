import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import GitLogArgs
from toolrunner.app.tools import git_log as git_log_module
from toolrunner.app.tools.git_log import run_git_log


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _response(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
):
    ok = exit_code == 0
    return JSONResponse(
        status_code=200 if ok else 400,
        content={
            "ok": ok,
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


def test_git_log_parses_commits(monkeypatch, tmp_path: Path):
    sample_output = "oid1\x00Alice\x00alice@example.com\x001600000000\x00Fix bug\n" "oid2\x00Bob\x00bob@example.com\x001600000100\x00Add feature\n"
    captured: dict[str, object] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return _response(stdout=sample_output)

    monkeypatch.setattr(git_log_module, "run_command", fake_run_command)
    args = GitLogArgs(max_count=5)
    response = run_git_log(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    result = payload["result"]
    assert len(result["commits"]) == 2
    first = result["commits"][0]
    assert first["oid"] == "oid1"
    assert first["author_name"] == "Alice"
    assert first["author_email"] == "alice@example.com"
    assert first["author_time_epoch"] == 1600000000
    assert first["subject"] == "Fix bug"
    assert captured["cmd"][1] == "log"
    assert "--max-count=5" in captured["cmd"]
    assert result["repo_dir"] == "."
    assert result["ref"] == "HEAD"
    assert result["max_count"] == 5
    assert result["raw"]["stdout_truncated"] is False
    assert result["raw"]["stderr"] == ""


def test_git_log_handles_malformed_line(monkeypatch, tmp_path: Path):
    sample_output = "malformed\n" "oidA\x00Name\x00email\x001600000000\x00Message\n"
    monkeypatch.setattr(
        git_log_module,
        "run_command",
        lambda run_dir, run_args: _response(stdout=sample_output),
    )
    args = GitLogArgs(max_count=1, ref="HEAD")
    response = run_git_log(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    assert len(payload["result"]["commits"]) == 1


def test_git_log_propagates_errors(monkeypatch, tmp_path: Path):
    def fake_run_command(run_dir, run_args):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "tool_runner.NOT_FOUND",
                    "message": "not a git repo",
                    "details": {},
                },
            },
        )

    monkeypatch.setattr(git_log_module, "run_command", fake_run_command)
    args = GitLogArgs()
    response = run_git_log(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")


def test_git_log_ref_cannot_start_dash(tmp_path: Path):
    args = GitLogArgs(ref="-bad")
    response = run_git_log(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")


def test_git_log_parse_warning_when_truncated(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        git_log_module,
        "run_command",
        lambda run_dir, run_args: _response(
            stdout="oid\x00A\x00a@e\x001600000000\x00Message\n",
            stdout_truncated=True,
        ),
    )
    response = run_git_log(tmp_path, GitLogArgs())
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["parse_warning"] == "stdout truncated; commits may be incomplete"
