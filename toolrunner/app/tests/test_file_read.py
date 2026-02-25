import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import FileReadArgs
from toolrunner.app.tools.file_read import read_file


def test_file_read_text(tmp_path: Path):
    file = tmp_path / "hello.txt"
    file.write_text("line1\nline2\nline3\n")
    args = FileReadArgs(path="hello.txt", mode="text", start_line=2, end_line=3, max_bytes=50)
    response = read_file(tmp_path, args)
    assert isinstance(response, JSONResponse)
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["result"]["content"] == "line2\nline3\n"
    assert payload["result"]["total_lines"] == 3
    assert payload["result"]["start_line"] == 2
    assert payload["result"]["end_line"] == 3


def test_file_read_binary(tmp_path: Path):
    file = tmp_path / "blob.bin"
    file.write_bytes(bytes(range(10)))
    args = FileReadArgs(path="blob.bin", mode="binary", max_bytes=5)
    response = read_file(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    assert payload["result"]["byte_length"] == 5
    assert payload["result"]["truncated"] is True


def test_file_read_path_escape(tmp_path: Path):
    args = FileReadArgs(path="../etc/passwd")
    response = read_file(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"] is False
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")


def test_file_read_not_found(tmp_path: Path):
    args = FileReadArgs(path="missing.txt")
    response = read_file(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["error"]["code"].endswith("NOT_FOUND")


def test_file_read_directory(tmp_path: Path):
    (tmp_path / "folder").mkdir()
    args = FileReadArgs(path="folder")
    response = read_file(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["error"]["code"].endswith("IS_DIRECTORY")
