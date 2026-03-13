import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import FileDeleteArgs, FileWriteArgs
from toolrunner.app.tools.file_delete import delete_file
from toolrunner.app.tools.file_write import write_file


def _json_response(response: JSONResponse):
    return json.loads(response.body)


def test_file_delete_repo_relative_path(tmp_path: Path):
    write_response = write_file(
        tmp_path,
        FileWriteArgs(path="nested/hello.txt", content="hello", overwrite=True),
    )
    assert _json_response(write_response)["ok"] is True

    response = delete_file(tmp_path, FileDeleteArgs(path="nested/hello.txt"))
    payload = _json_response(response)

    assert payload["ok"] is True
    assert payload["result"]["deleted"] is True
    assert payload["result"]["deleted_type"] == "file"
    assert (tmp_path / "nested" / "hello.txt").exists() is False


def test_file_delete_absolute_path_with_policy_allowed_root(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    target = tmp_path / "absolute.txt"
    write_response = write_file(
        run_dir,
        FileWriteArgs(path=str(target), content="hello", overwrite=True),
        policy={"allowed_roots": [str(tmp_path)]},
    )
    assert _json_response(write_response)["ok"] is True

    response = delete_file(
        run_dir,
        FileDeleteArgs(path=str(target)),
        policy={"allowed_roots": [str(tmp_path)]},
    )
    payload = _json_response(response)

    assert payload["ok"] is True
    assert payload["result"]["deleted"] is True
    assert payload["result"]["resolved_path"] == str(target.resolve())
    assert target.exists() is False
