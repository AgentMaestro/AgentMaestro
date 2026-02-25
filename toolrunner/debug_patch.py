from pathlib import Path
from toolrunner.app.tools.file_patch import apply_patch
from toolrunner.app.models import FilePatchArgs
path = Path('tmp_test')
if path.exists():
    import shutil
    shutil.rmtree(path)
path.mkdir()
(file := path / 'target.txt').write_text('old\n')
args = FilePatchArgs(path='target.txt', patch_unified='''--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-old
+new
''', expected_sha256=(lambda p: __import__('hashlib').sha256(p.read_bytes()).hexdigest())(file))
response = apply_patch(path, args)
print(response.json() if hasattr(response, 'json') else response.body)
