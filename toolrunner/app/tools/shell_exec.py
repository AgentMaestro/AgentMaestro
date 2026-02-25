from __future__ import annotations

import subprocess
from pathlib import Path

from ..limits import truncate_output, validate_command
from ..sandbox import safe_join


def run_shell(
    run_dir: Path,
    cmd: list[str],
    cwd: str,
    timeout_s: int,
    max_output_bytes: int,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str]:
    if not cmd:
        raise ValueError("cmd is required")
    validate_command(cmd[0])
    working_dir = safe_join(run_dir, cwd or ".")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            shell=False,
            cwd=working_dir,
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
