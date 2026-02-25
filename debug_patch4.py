import hashlib
from pathlib import Path
import subprocess

tmp = Path("toolrunner/tmp_patch4")
tmp.mkdir(exist_ok=True)
path = tmp / "target.txt"
path.write_text("old\n")
patch = """--- a/target.txt
++ b/target.txt
@@ -1 +1 @@
-old
+new
"""
patch_file = tmp / "p.patch"
patch_file.write_text(patch)
subprocess.run(["git", "-C", str(tmp), "init"], capture_output=True)
result = subprocess.run(
    [
        "git",
        "-C",
        str(tmp),
        "apply",
        "--reject",
        "--unidiff-zero",
        "--whitespace=nowarn",
        "-p0",
        str(patch_file),
    ],
    capture_output=True,
    text=True,
)
print(result.returncode)
print(result.stderr)
