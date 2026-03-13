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
@@ -1,1 +1,1 @@
-old
+new
"""

PATCH_FAIL = """--- a/target.txt
+++ b/target.txt
@@ -1,1 +1,1 @@
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


def test_file_patch_absolute_path_requires_allow_write(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    path = Path("C:/agentmaestro-forbidden/target.txt")
    args = FilePatchArgs(path=str(path), patch_unified=PATCH)
    response = apply_patch(run_dir, args, policy={"allow_write": False})
    payload = _json_response(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_NOT_ALLOWED")


def test_file_patch_absolute_path_with_allow_write(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    path = tmp_path / "target.txt"
    path.write_text("old\n")
    args = FilePatchArgs(path=str(path), patch_unified=PATCH, expected_sha256=_sha(path))
    response = apply_patch(run_dir, args, policy={"allow_write": True})
    payload = _json_response(response)
    assert payload["ok"]
    assert payload["result"]["hunks_applied"] == payload["result"]["hunks_total"]
    assert path.read_text().strip() == "new"
    assert payload["result"]["backup_path"]


def test_file_patch_absolute_path_with_policy_allowed_root(tmp_path: Path):
    run_dir = tmp_path / "sandbox" / "workspace"
    run_dir.mkdir(parents=True)
    path = tmp_path / "target.txt"
    path.write_text("old\n")
    args = FilePatchArgs(path=str(path), patch_unified=PATCH, expected_sha256=_sha(path))
    response = apply_patch(run_dir, args, policy={"allowed_roots": [str(tmp_path)]})
    payload = _json_response(response)
    assert payload["ok"]
    assert path.read_text().strip() == "new"


def test_file_patch_requires_explicit_hunk_ranges(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("old\n")
    patch = """--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-old
+new
"""
    args = FilePatchArgs(path="target.txt", patch_unified=patch)
    response = apply_patch(tmp_path, args)
    payload = _json_response(response)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATCH_FAILED")
    assert "explicit ranges" in payload["error"]["message"]


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()
