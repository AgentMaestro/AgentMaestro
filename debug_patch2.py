import json
from pathlib import Path

from toolrunner.app.models import FilePatchArgs
from toolrunner.app.tools.file_patch import apply_patch

tmp = Path("toolrunner/tmp_patch2")
tmp.mkdir(exist_ok=True)
path = tmp / "target.txt"
path.write_text("old\n")
args = FilePatchArgs(path="target.txt", patch_unified="""--- a/target.txt
++ b/target.txt
@@
-old
+new
""")
response = apply_patch(tmp, args)
print(response.status_code)
print(response.body)
