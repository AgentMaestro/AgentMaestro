import json
from pathlib import Path

from toolrunner.app.models import RepoTreeArgs
from toolrunner.app.tools.repo_tree import list_repo_tree


def _payload(response):
    return json.loads(response.body)


def test_repo_tree_basic(tmp_path: Path):
    (tmp_path / "alpha" / "beta").mkdir(parents=True)
    (tmp_path / "alpha" / "beta" / "foo.txt").write_text("foo")
    (tmp_path / "root.txt").write_text("root")
    response = list_repo_tree(tmp_path, RepoTreeArgs())
    payload = _payload(response)
    entries = payload["result"]["entries"]
    assert payload["ok"]
    assert payload["result"]["stats"]["dirs"] == 2
    assert payload["result"]["stats"]["files"] == 2
    assert entries[0]["path"] == "alpha"
    assert entries[1]["path"] == "alpha/beta"
    assert entries[2]["path"] == "alpha/beta/foo.txt"
    assert entries[2]["depth"] == 3
    assert entries[-1]["path"] == "root.txt"
    assert "size_bytes" in entries[0]
    assert entries[-1]["size_bytes"] == 4


def test_repo_tree_max_depth(tmp_path: Path):
    (tmp_path / "alpha" / "beta").mkdir(parents=True)
    (tmp_path / "alpha" / "beta" / "foo.txt").write_text("foo")
    (tmp_path / "root.txt").write_text("root")
    response = list_repo_tree(tmp_path, RepoTreeArgs(max_depth=2))
    payload = _payload(response)
    candidates = [entry["path"] for entry in payload["result"]["entries"]]
    assert "alpha/beta/foo.txt" not in candidates
    assert payload["result"]["stats"]["files"] == 1
    assert payload["result"]["stats"]["dirs"] == 2


def test_repo_tree_max_entries(tmp_path: Path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name)
    response = list_repo_tree(tmp_path, RepoTreeArgs(max_entries=2))
    payload = _payload(response)
    entries = payload["result"]["entries"]
    assert len(entries) == 2
    assert payload["result"]["truncated"] is True
    assert entries[0]["path"] == "a.txt"
    assert entries[1]["path"] == "b.txt"
    assert payload["result"]["stats"]["entries"] == 2


def test_repo_tree_include_exclude_globs(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("skip")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# doc")
    (tmp_path / "notes.txt").write_text("keep")
    response = list_repo_tree(tmp_path, RepoTreeArgs(include_globs=["**/*.md"]))
    payload = _payload(response)
    entries = [entry["path"] for entry in payload["result"]["entries"]]
    assert entries == ["docs/README.md"]
    assert ".git/config" not in entries
    assert payload["result"]["stats"]["files"] == 1


def test_repo_tree_without_metadata(tmp_path: Path):
    (tmp_path / "meta.txt").write_text("payload")
    response = list_repo_tree(tmp_path, RepoTreeArgs(include_metadata=False))
    payload = _payload(response)
    entry = payload["result"]["entries"][0]
    assert "size_bytes" not in entry
    assert "mtime_epoch" not in entry
