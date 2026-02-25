import base64
import subprocess
from pathlib import Path

import pytest

from toolrunner.app.models import PythonArgs, PythonFileItem
from toolrunner.app.tools.python_exec import run_python


def fake_subprocess(stdout="ok", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_python_snippet(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: fake_subprocess(stdout="snippet"))
    args = PythonArgs(code="print('hai')")
    code, out, err = run_python(tmp_path, args, timeout_s=5, max_output_bytes=256)
    assert code == 0
    assert "snippet" in out
    assert err == ""


def test_python_timeout(monkeypatch, tmp_path):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=5)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    args = PythonArgs(code="print('loop')")
    code, out, err = run_python(tmp_path, args, timeout_s=5, max_output_bytes=10)
    assert code is None
    assert out == ""


def test_python_files(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return fake_subprocess(stdout="files")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content = base64.b64encode(b"print('from file')").decode("utf-8")
    file_item = PythonFileItem(path="scripts/run.py", content_b64=content)
    args = PythonArgs(files=[file_item], entrypoint="scripts/run.py")
    code, out, err = run_python(tmp_path, args, timeout_s=5, max_output_bytes=256)
    assert code == 0
    assert "files" in out
    target = tmp_path / "scripts" / "run.py"
    assert target.exists()


def test_python_file_traversal_rejected(tmp_path):
    file_item = PythonFileItem(path="../escape/run.py", content_b64=base64.b64encode(b"print('bad')").decode("utf-8"))
    args = PythonArgs(files=[file_item], entrypoint="../escape/run.py")
    with pytest.raises(ValueError):
        run_python(tmp_path, args, timeout_s=5, max_output_bytes=256)
