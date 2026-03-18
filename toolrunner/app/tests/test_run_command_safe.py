import json
from pathlib import Path

from toolrunner.app.models import RunCommandSafeArgs
import toolrunner.app.tools.run_command_safe as run_command_safe_module
from toolrunner.app.tools.run_command_safe import run_command_safe
from toolrunner.app.tools.subprocess_utils import SubprocessExecutionResult


def _payload(response):
    return json.loads(response.body)


def _policy(root: Path) -> dict[str, object]:
    return {"repo_root": str(root), "allowed_roots": [str(root)]}


def test_run_command_safe_allows_manage_check(tmp_path: Path, monkeypatch):
    (tmp_path / "manage.py").write_text("print('ok')\n", encoding="utf-8")

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=0,
            stdout="system check identified no issues\n",
            stderr="",
            duration_ms=12,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_command_safe_module, "run_subprocess", fake_run_subprocess)
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["python", "manage.py", "check"], cwd="."),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["ok"] is True
    assert payload["result"]["normalized_command"] == ["python", "manage.py", "check"]


def test_run_command_safe_rejects_git(tmp_path: Path):
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["git", "status"], cwd="."),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["result"]["policy_reason"] == "git commands are not allowed via run_command_safe. Use git_* tools instead."


def test_run_command_safe_rejects_shell_composition(tmp_path: Path):
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["python", "-m", "pytest", "tests&&more"], cwd="."),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert not payload["ok"]
    assert "shell composition" in payload["result"]["policy_reason"]


def test_run_command_safe_rejects_unknown_executable(tmp_path: Path):
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["node", "script.js"], cwd="."),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert not payload["ok"]
    assert "not allowed" in payload["result"]["policy_reason"]


def test_run_command_safe_rejects_workspace_escape(tmp_path: Path):
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["pytest", "tests/test_sample.py"], cwd="../outside"),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert not payload["ok"]
    assert "path traversal" in payload["result"]["policy_reason"]


def test_run_command_safe_timeout_sets_structured_result(tmp_path: Path, monkeypatch):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")

    def fake_run_subprocess(command, *, cwd, timeout_seconds, max_output_bytes, stdin_text=None, env=None):
        return SubprocessExecutionResult(
            command=list(command),
            cwd=str(cwd),
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=99,
            timed_out=True,
            stdout_truncated=False,
            stderr_truncated=False,
            truncated=False,
            timeout_seconds=timeout_seconds,
            timeout_source="args.timeout_seconds",
        )

    monkeypatch.setattr(run_command_safe_module, "run_subprocess", fake_run_subprocess)
    response = run_command_safe(
        tmp_path,
        RunCommandSafeArgs(argv=["pytest", "tests/test_sample.py"], cwd=".", timeout_seconds=3),
        _policy(tmp_path),
    )
    payload = _payload(response)
    assert payload["ok"]
    assert payload["result"]["timed_out"] is True
    assert payload["result"]["exit_code"] is None

