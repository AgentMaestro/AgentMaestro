import subprocess
from pathlib import Path

import pytest

from toolrunner.app.config import ALLOWED_COMMANDS
from toolrunner.app.tools.shell_exec import run_shell


def test_shell_exec_allowed(monkeypatch, tmp_path):
    seen = {}
    allowed_cmd = ALLOWED_COMMANDS[0]

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out, err, working_dir = run_shell(
        tmp_path,
        [allowed_cmd, "-q"],
        cwd=".",
        timeout_s=5,
        max_output_bytes=128,
        env={"FOO": "1"},
    )
    assert code == 0
    assert "ok" in out
    assert err == ""
    assert seen["cwd"] == tmp_path
    assert seen["cmd"] == [allowed_cmd, "-q"]
    assert working_dir == tmp_path


def test_shell_exec_blocked(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_shell(tmp_path, ["bash"], cwd=".", timeout_s=5, max_output_bytes=128)


def test_shell_exec_timeout(monkeypatch, tmp_path):
    allowed_cmd = ALLOWED_COMMANDS[0]

    def fake_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="cmd", timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    code, out, err, _ = run_shell(tmp_path, [allowed_cmd], cwd=".", timeout_s=5, max_output_bytes=128)
    assert code is None
    assert err == ""


def test_shell_exec_truncates_output(monkeypatch, tmp_path):
    long_text = "x" * 500
    allowed_cmd = ALLOWED_COMMANDS[0]

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=long_text, stderr="err" * 100
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out, err, _ = run_shell(tmp_path, [allowed_cmd], cwd=".", timeout_s=5, max_output_bytes=10)
    assert code == 0
    assert out.endswith("…")
    assert len(out) <= 11
    assert err.endswith("…")


def test_shell_exec_repo_root_relative_cwd_with_policy(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    seen = {}
    allowed_cmd = ALLOWED_COMMANDS[0]

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out, err, working_dir = run_shell(
        tmp_path,
        [allowed_cmd, "-q"],
        cwd=".",
        timeout_s=5,
        max_output_bytes=128,
        policy={"repo_root": str(repo_root), "allowed_roots": [str(repo_root)]},
    )
    assert code == 0
    assert out == "ok"
    assert err == ""
    assert seen["cwd"] == repo_root.resolve()
    assert working_dir == repo_root.resolve()
