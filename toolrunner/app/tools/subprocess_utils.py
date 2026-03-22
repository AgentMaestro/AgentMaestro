from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SubprocessExecutionResult:
    command: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    truncated: bool
    timeout_seconds: int | None
    timeout_source: str | None


def timeout_details(
    timeout_seconds: int | None, source: str | None
) -> dict[str, int | float | str | None]:
    if timeout_seconds is None:
        return {
            "timeout_ms": None,
            "timeout_seconds": None,
            "timeout_source": source,
        }
    return {
        "timeout_ms": timeout_seconds * 1000,
        "timeout_seconds": timeout_seconds,
        "timeout_source": source,
    }


def truncate_output(payload: bytes | str | None, max_bytes: int) -> tuple[str, bool]:
    if payload is None:
        return "", False
    if isinstance(payload, str):
        data = payload.encode("utf-8", errors="replace")
    else:
        data = payload
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace"), False
    ellipsis = "…"
    ellipsis_bytes = ellipsis.encode("utf-8")
    available = max(max_bytes - len(ellipsis_bytes), 0)
    truncated_data = data[:available]
    safe_bytes = truncated_data
    safe_text = ""
    while safe_bytes:
        try:
            safe_text = safe_bytes.decode("utf-8")
            break
        except UnicodeDecodeError as exc:
            safe_bytes = safe_bytes[: exc.start]
    else:
        safe_text = ""
    return safe_text + ellipsis, True


def terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass
        return
    try:
        proc.kill()
    except Exception:
        pass


def run_subprocess(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | None,
    max_output_bytes: int,
    stdin_text: str | None = None,
) -> SubprocessExecutionResult:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    input_data = stdin_text.encode("utf-8") if stdin_text is not None else None
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None
    exit_code: int | None = None
    timed_out = False
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start = time.monotonic()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_data is not None else None,
            cwd=cwd,
            env=merged_env,
            creationflags=creationflags,
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=input_data,
                timeout=timeout_seconds,
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            terminate_tree(proc)
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                stdout_bytes = exc.stdout or b""
                stderr_bytes = exc.stderr or b""
            finally:
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
    finally:
        duration_ms = int(round((time.monotonic() - start) * 1000))

    stdout, stdout_truncated = truncate_output(stdout_bytes, max_output_bytes)
    stderr, stderr_truncated = truncate_output(stderr_bytes, max_output_bytes)
    return SubprocessExecutionResult(
        command=list(command),
        cwd=str(cwd),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        truncated=stdout_truncated or stderr_truncated,
        timeout_seconds=timeout_seconds,
        timeout_source=None,
    )
