from __future__ import annotations

import json
import time

from fastapi import FastAPI, Depends, HTTPException, Request, status

from .auth import verify_signature
from .models import (
    ExecuteRequest,
    ExecuteResponse,
    FileReadArgs,
    FileWriteArgs,
    PythonArgs,
    RepoTreeArgs,
    ShellArgs,
)
from .sandbox import get_run_dir
from .tools import (
    create_webhook,
    list_repo_tree,
    read_file,
    run_python,
    run_shell,
    write_file,
)

app = FastAPI()


@app.middleware("http")
async def read_body(request: Request, call_next):
    request.state._body = await request.body()
    return await call_next(request)



@app.post("/v1/execute")
async def execute(request: Request, raw=Depends(verify_signature)):
    payload = ExecuteRequest(**json.loads(raw.decode("utf-8")))
    run_dir = get_run_dir(payload.workspace_id, payload.run_id)
    if payload.tool_name == "file_read":
        file_args = FileReadArgs(**payload.args)
        return read_file(run_dir, file_args)
    if payload.tool_name == "file_write":
        write_args = FileWriteArgs(**payload.args)
        return write_file(run_dir, write_args)
    if payload.tool_name == "repo_tree":
        tree_args = RepoTreeArgs(**payload.args)
        return list_repo_tree(run_dir, tree_args)
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    result: dict[str, object] | None = {"tool": payload.tool_name}
    if payload.policy:
        result["policy"] = payload.policy
    duration_ms = 0
    start = time.monotonic()
    try:
        if payload.tool_name == "shell_exec":
            shell = ShellArgs(**payload.args)
            exit_code, stdout, stderr = run_shell(
                run_dir,
                shell.cmd,
                shell.cwd,
                payload.limits.timeout_s,
                payload.limits.max_output_bytes,
                env=shell.env,
            )
        elif payload.tool_name == "python_exec":
            python_args = PythonArgs(**payload.args)
            exit_code, stdout, stderr = run_python(
                run_dir,
                python_args,
                payload.limits.timeout_s,
                payload.limits.max_output_bytes,
            )
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid tool")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unexpected error") from exc
    finally:
        duration_ms = int(round((time.monotonic() - start) * 1000))
    status_text = "COMPLETED" if exit_code == 0 else "FAILED"
    return ExecuteResponse(
        request_id=payload.request_id,
        status=status_text,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        result=result,
    )


@app.post("/v1/webhook", response_model=ExecuteResponse)
async def webhook_endpoint(request: Request, raw=Depends(verify_signature)):
    payload = json.loads(raw.decode("utf-8"))
    return create_webhook(payload)
