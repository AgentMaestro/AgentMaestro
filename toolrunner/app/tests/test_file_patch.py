import base64
import json
import hashlib

from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import FilePatchArgs
from toolrunner.app.tools.file_patch import apply_patch


def _json_response(response: JSONResponse):
    return json.loads(response.body)


PATCH = """--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-old
+new
"""

PATCH_FAIL = """--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-missing
+added
"""


def test_file_patch_success(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("old\n")
    args = FilePatchArgs(path="target.txt", patch_unified=PATCH, expected_sha256=_sha(path))
    response = apply_patch(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"]
    assert payload["result"]["hunks_applied"] == payload["result"]["hunks_total"]
    assert path.read_text().strip() == "new"
    assert payload["result"]["backup_path"]


def test_file_patch_conflict(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("other")
    args = FilePatchArgs(path="target.txt", patch_unified=PATCH, expected_sha256="deadbeef")
    response = apply_patch(tmp_path, args)
    payload = _json_response(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("CONFLICT")


def test_file_patch_partial(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("old\n")
    args = FilePatchArgs(path="target.txt", patch_unified=PATCH_FAIL, fail_on_reject=False)
    response = apply_patch(tmp_path, args)
    payload = _json_response(response)
    assert payload["ok"]
    assert payload["result"]["applied_partially"]
    assert payload["result"]["rejects_path"]


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()
