from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET = os.environ.get("AGENTMAESTRO_TOOLRUNNER_SECRET", "insecure-secret").encode("utf-8")
SANDBOX_ROOT = Path(
    os.environ.get(
        "AGENTMAESTRO_TOOLRUNNER_SANDBOX_ROOT",
        str(Path(BASE_DIR, "sandbox").resolve()),
    )
)
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
TIMESTAMP_SKEW_SECONDS = int(os.environ.get("AGENTMAESTRO_TOOLRUNNER_TIMESTAMP_SKEW_SECONDS", "60"))

COMMAND_TIMEOUT = int(os.environ.get("AGENTMAESTRO_TOOLRUNNER_COMMAND_TIMEOUT", "30"))
OUTPUT_LIMIT = int(os.environ.get("AGENTMAESTRO_TOOLRUNNER_OUTPUT_LIMIT", "4096"))
ALLOWED_COMMANDS = [
    part.strip()
    for part in os.environ.get(
        "AGENTMAESTRO_TOOLRUNNER_ALLOWED_COMMANDS", "pytest,python,ruff,black,git,ls,cat"
    ).split(",")
    if part.strip()
]
PYTHON_INTERPRETER = os.environ.get("AGENTMAESTRO_TOOLRUNNER_PYTHON", "python3")
