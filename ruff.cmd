@echo off
python -c "import json, sys; json.dump([{'code': 'F401', 'message': \"Unused import 'os'\", 'location': {'row': 1, 'column': 1}, 'end_location': {'row': 1, 'column': 12}, 'fix': None, 'filename': 'smoke/lint-runner/bad_lint.py'}], sys.stdout); sys.exit(1)"
