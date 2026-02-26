import json
from fastapi.responses import JSONResponse

from toolrunner.app.models import GitCheckoutArgs, RunCommandArgs
from toolrunner.app.tools import git_checkout as git_checkout_module
from toolrunner.app.tools.git_checkout import run_git_checkout


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _successful_response(stdout: str = "", stderr: str = ""):
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


def _error_response(message: str = "oops"):
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "error": {
                "code": "tool_runner.INVALID_ARGUMENT",
                "message": message,
                "details": {},
            },
        },
    )


def test_git_checkout_switch_branch(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        captured["cwd"] = run_args.cwd
        captured["timeout_ms"] = run_args.timeout_ms
        captured["max_output_bytes"] = run_args.max_output_bytes
        return _successful_response(stdout="Switched to branch 'main'\n")

    monkeypatch.setattr(git_checkout_module, "run_command", fake_run_command)
    args = GitCheckoutArgs(ref="main")
    response = run_git_checkout(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    assert payload["result"]["ref"] == "main"
    assert not payload["result"]["detached"]
    assert payload["result"]["repo_dir"] == "."
    assert ["git", "checkout", "--", "main"] == captured["cmd"]
    assert captured["cwd"] == "."
    assert captured["max_output_bytes"] == args.max_output_bytes


def test_git_checkout_create_branch(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run_command(run_dir, run_args):
        captured["cmd"] = run_args.cmd
        return _successful_response(stdout="Switched to a new branch 'feature'\n")

    monkeypatch.setattr(git_checkout_module, "run_command", fake_run_command)
    args = GitCheckoutArgs(ref="feature", create=True)
    response = run_git_checkout(tmp_path, args)
    payload = _payload(response)

    assert payload["ok"]
    assert payload["result"]["detached"] is False
    assert captured["cmd"] == ["git", "checkout", "-b", "feature"]


def test_git_checkout_detached(monkeypatch, tmp_path):
    def fake_run_command(run_dir, run_args):
        return _successful_response(
            stdout="Note: switching to 'deadbeef'\nYou are in 'detached HEAD' state.\n"
        )

    monkeypatch.setattr(git_checkout_module, "run_command", fake_run_command)
    args = GitCheckoutArgs(ref="deadbeef")
    response = run_git_checkout(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["detached"]


def test_git_checkout_propagates_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(git_checkout_module, "run_command", lambda run_dir, run_args: _error_response())
    args = GitCheckoutArgs(ref="main")
    response = run_git_checkout(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")
