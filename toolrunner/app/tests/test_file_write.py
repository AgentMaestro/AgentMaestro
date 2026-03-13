import base64
import hashlib
import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import FileWriteArgs
from toolrunner.app.tools.file_write import write_file


def _json_response(response: JSONResponse):
    return json.loads(response.body)


def test_file_write_text(tmp_path: Path):
    args = FileWriteArgs(path="dir/hello.txt", content="hello", encoding="utf-8")
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["created"]
    assert result["overwritten"] is False
    assert (tmp_path / "dir" / "hello.txt").read_text() == "hello"


def test_file_write_binary(tmp_path: Path):
    data_bytes = b"hello"
    encoded = base64.b64encode(data_bytes).decode()
    args = FileWriteArgs(path="data.bin", mode="binary", content_base64=encoded)
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["bytes_written"] == len(data_bytes)
    assert (tmp_path / "data.bin").read_bytes() == data_bytes


def test_file_write_absolute_path_requires_allow_write(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    target = Path("C:/agentmaestro-forbidden/absolute.txt")
    args = FileWriteArgs(path=str(target), content="hello", overwrite=True)
    response = write_file(run_dir, args)
    payload = _json_response(response)
    assert payload["ok"] is False
    assert payload["error"]["code"].endswith("PATH_NOT_ALLOWED")


def test_file_write_absolute_path_with_allow_write(tmp_path: Path):
    target = tmp_path / "absolute.txt"
    args = FileWriteArgs(path=str(target), content="hello", overwrite=True)
    response = write_file(tmp_path, args, policy={"allow_write": True})
    payload = _json_response(response)
    assert payload["ok"] is True
    assert target.read_text() == "hello"


def test_file_write_absolute_path_with_policy_allowed_root(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    target = tmp_path / "outside.txt"
    args = FileWriteArgs(path=str(target), content="hello", overwrite=True)
    response = write_file(run_dir, args, policy={"allowed_roots": [str(tmp_path)]})
    payload = _json_response(response)
    assert payload["ok"] is True
    assert target.read_text() == "hello"


def test_file_write_overwrite_false(tmp_path: Path):
    file = tmp_path / "exists.txt"
    file.write_text("old")
    args = FileWriteArgs(path="exists.txt", content="new", overwrite=False)
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"] is False
    assert payload["error"]["code"].endswith("ALREADY_EXISTS")


def test_file_write_expected_sha_conflict(tmp_path: Path):
    file = tmp_path / "conf.txt"
    file.write_text("existing")
    sha = hashlib.sha256("different".encode("utf-8")).hexdigest()
    args = FileWriteArgs(path="conf.txt", content="sorry", expected_sha256=sha, overwrite=True)
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["error"]["code"].endswith("CONFLICT")


def test_file_write_expected_sha_ok(tmp_path: Path):
    file = tmp_path / "conf.txt"
    file.write_text("existing")
    sha = hashlib.sha256(file.read_bytes()).hexdigest()
    args = FileWriteArgs(path="conf.txt", content="updated", expected_sha256=sha, overwrite=True)
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"]
    assert payload["result"]["overwritten"]


def test_file_write_make_dirs_false(tmp_path: Path):
    args = FileWriteArgs(path="nested/new.txt", content="hi", make_dirs=False)
    response = write_file(tmp_path, args)
    payload = _json_response(response)
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")


def test_file_write_repo_root_relative_path_with_policy(tmp_path: Path):
    repo_root = tmp_path / "repo"
    run_dir = tmp_path / "sandbox" / "workspace"
    repo_root.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    response = write_file(
        run_dir,
        FileWriteArgs(path="notes/todo.txt", content="repo-root write", overwrite=True),
        policy={"repo_root": str(repo_root), "allowed_roots": [str(repo_root)]},
    )
    payload = _json_response(response)
    assert payload["ok"]
    assert (repo_root / "notes" / "todo.txt").read_text() == "repo-root write"
