import json
import sys
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import RunCommandArgs
from toolrunner.app.tools.run_command import run_command


def _payload(response: JSONResponse) -> dict:
    return json.loads(response.body)


def test_run_command_success(tmp_path: Path):
    args = RunCommandArgs(cmd=[sys.executable, "-c", "print('hello world')"], cwd=".")
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["exit_code"] == 0
    assert "hello world" in result["stdout"].strip()
    assert not result["timed_out"]
    assert not result["stdout_truncated"]


def test_run_command_env_and_cwd(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    args = RunCommandArgs(
        cmd=[sys.executable, "-c", "import os; print(os.getenv('FOO'))"],
        cwd="subdir",
        env={"FOO": "value"},
    )
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["stdout"].strip() == "value"


def test_run_command_with_stdin(tmp_path: Path):
    args = RunCommandArgs(
        cmd=[sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        stdin_text="line1\nline2",
    )
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    assert "line1" in payload["result"]["stdout"]


def test_run_command_truncation_respects_bytes(tmp_path: Path):
    args = RunCommandArgs(
        cmd=[sys.executable, "-c", "print('\\u20AC' * 20)"],
        max_output_bytes=10,
    )
    response = run_command(tmp_path, args)
    payload = _payload(response)
    result = payload["result"]
    stdout = result["stdout"]
    assert result["stdout_truncated"]
    assert stdout.endswith("\u2026")
    assert len(stdout.encode("utf-8")) <= args.max_output_bytes


def test_run_command_timeout_kills(tmp_path: Path):
    args = RunCommandArgs(
        cmd=[
            sys.executable,
            "-c",
            "import subprocess, time, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)']); time.sleep(5)",
        ],
        timeout_ms=10,
    )
    response = run_command(tmp_path, args)
    payload = _payload(response)
    result = payload["result"]
    assert payload["ok"]
    assert result["timed_out"]
    assert result["exit_code"] is None


def test_run_command_nonexistent_cwd(tmp_path: Path):
    args = RunCommandArgs(
        cmd=[sys.executable, "-c", "print('ok')"],
        cwd="does-not-exist",
    )
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")
    assert payload["error"]["details"]["cwd"] == "does-not-exist"


def test_run_command_path_escape(tmp_path: Path):
    args = RunCommandArgs(cmd=[sys.executable, "-c", "print('ok')"], cwd="../outside")
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")


def test_run_command_command_missing(tmp_path: Path):
    args = RunCommandArgs(cmd=["nonexistent-command-xyz"])
    response = run_command(tmp_path, args)
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")
    assert payload["error"]["details"].get("cmd0") == "nonexistent-command-xyz"
