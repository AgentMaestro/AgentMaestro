from pathlib import Path

import pytest

from toolrunner.app import config
from toolrunner.app.sandbox import get_run_dir, safe_join


def test_get_run_dir_creates_path(tmp_path, monkeypatch):
    sandbox_root = tmp_path / "sandbox"
    monkeypatch.setattr(config, "SANDBOX_ROOT", sandbox_root)
    run_dir = get_run_dir("ws", "run")
    assert run_dir.exists()
    assert run_dir.name == "run"


def test_safe_join_rejects_traversal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        safe_join(base, "../escape.txt")


def test_safe_join_rejects_absolute(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        safe_join(base, Path("/etc/passwd"))


def test_safe_join_allows_relative(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    target = safe_join(base, "file.txt")
    assert base in target.parents
