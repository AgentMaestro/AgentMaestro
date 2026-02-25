from pathlib import Path
from toolrunner.app.tools.file_patch import apply_patch
from toolrunner.app.models import FilePatchArgs
path = Path('toolrunner/tmp_test_partial_manual')
import shutil
if path.exists():
    shutil.rmtree(path)
path.mkdir(parents=True, exist_ok=True)
(file := path / 'target.txt').write_text('old\n')
args = FilePatchArgs(path='target.txt', patch_unified='''--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-missing
+added
''', fail_on_reject=False)
response = apply_patch(path, args)
print(response.body)
