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
