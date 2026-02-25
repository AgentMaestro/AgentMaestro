import subprocess
from pathlib import Path

import pytest

from toolrunner.app.tools.shell_exec import run_shell


def test_shell_exec_allowed(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out, err = run_shell(
        tmp_path,
        ["pytest", "-q"],
        cwd=".",
        timeout_s=5,
        max_output_bytes=128,
        env={"FOO": "1"},
    )
    assert code == 0
    assert "ok" in out
    assert err == ""
    assert seen["cwd"] == tmp_path
    assert seen["cmd"] == ["pytest", "-q"]


def test_shell_exec_blocked(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        run_shell(tmp_path, ["bash"], cwd=".", timeout_s=5, max_output_bytes=128)


def test_shell_exec_timeout(monkeypatch, tmp_path):
    def fake_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="cmd", timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_timeout)
    code, out, err = run_shell(tmp_path, ["pytest"], cwd=".", timeout_s=5, max_output_bytes=128)
    assert code is None
    assert err == ""


def test_shell_exec_truncates_output(monkeypatch, tmp_path):
    long_text = "x" * 500

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=long_text, stderr="err" * 100
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out, err = run_shell(tmp_path, ["pytest"], cwd=".", timeout_s=5, max_output_bytes=10)
    assert code == 0
    assert out.endswith("…")
    assert len(out) <= 11
    assert err.endswith("…")
