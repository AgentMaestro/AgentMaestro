import json
import sys
from pathlib import Path

ISSUE = {
    "code": "F401",
    "message": "Unused import 'os'",
    "location": {"row": 1, "column": 1},
    "end_location": {"row": 1, "column": 12},
    "fix": None,
    "filename": "smoke/lint-runner/bad_lint.py",
}


def main() -> None:
    json.dump([ISSUE], sys.stdout)
    sys.exit(1)


if __name__ == "__main__":
    main()
