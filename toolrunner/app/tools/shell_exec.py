from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..config import resolve_policy_path
from ..limits import truncate_output, validate_command


def run_shell(
    run_dir: Path,
    cmd: list[str],
    cwd: str,
    timeout_s: int,
    max_output_bytes: int,
    env: dict[str, str] | None = None,
    policy: dict | None = None,
) -> tuple[int | None, str, str, Path]:
    if not cmd:
        raise ValueError("cmd is required")
    validate_command(cmd[0])
    working_dir = resolve_policy_path(run_dir, cwd or ".", policy)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            shell=False,
            cwd=working_dir,
            env=merged_env,
            text=True,
        )
        return (
            completed.returncode,
            truncate_output(completed.stdout, max_output_bytes),
            truncate_output(completed.stderr, max_output_bytes),
            working_dir,
        )
    except subprocess.TimeoutExpired as exc:
        return (
            None,
            truncate_output(exc.stdout or "", max_output_bytes),
            truncate_output(exc.stderr or "", max_output_bytes),
            working_dir,
        )
