from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from ..config import PYTHON_INTERPRETER
from ..limits import truncate_output
from ..sandbox import safe_join
from ..models import PythonArgs


def run_python(
    run_dir: Path,
    args: PythonArgs,
    timeout_s: int,
    max_output_bytes: int,
) -> tuple[int | None, str, str]:
    if args.files:
        for file in args.files:
            target = safe_join(run_dir, file.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(file.content_b64))
    if args.entrypoint:
        main_file = safe_join(run_dir, args.entrypoint)
        if not main_file.exists():
            raise FileNotFoundError("entrypoint not written")
        cmd = [PYTHON_INTERPRETER, "-I", str(main_file)]
    else:
        cmd = [PYTHON_INTERPRETER, "-I", "-c", args.code or ""]
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    try:
        completed = subprocess.run(
            cmd,
            cwd=run_dir,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
            text=True,
        )
        return (
            completed.returncode,
            truncate_output(completed.stdout, max_output_bytes),
            truncate_output(completed.stderr, max_output_bytes),
        )
    except subprocess.TimeoutExpired as exc:
        return (
            None,
            truncate_output(exc.stdout or "", max_output_bytes),
            truncate_output(exc.stderr or "", max_output_bytes),
        )
