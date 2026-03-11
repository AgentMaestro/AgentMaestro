import hmac
import json
import time
import uuid
from hashlib import sha256
from typing import Any, Dict, Optional

import httpx
from django.conf import settings


def _sign(body: bytes, secret: bytes) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    message = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(secret, message, sha256).hexdigest()
    return timestamp, signature


async def run_tool(
    tool_name: str, args: Dict[str, Any], *, orchestration_run_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute a tool call in ToolRunner.

    Returns:
        {
          "ok": bool,
          "result": Any,
          "meta": dict,
          "error": Optional[str],
        }
    """
    base_url = getattr(settings, "TOOLRUNNER_URL", None) or getattr(settings, "TOOLRUNNER_URL")
    base_url = base_url.rstrip("/")
    secret_value = getattr(settings, "TOOLRUNNER_SECRET", None) or getattr(settings, "TOOLRUNNER_SECRET", "insecure-secret")
    secret = secret_value.encode("utf-8")
    timeout = getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", None) or getattr(settings, "TOOLRUNNER_HTTP_TIMEOUT", 45)
    request_id = str(uuid.uuid4())
    workspace_id = str(orchestration_run_id or "llm-workspace")
    run_folder = orchestration_run_id or request_id

    payload = {
        "request_id": request_id,
        "workspace_id": workspace_id,
        "run_id": run_folder,
        "tool_name": tool_name,
        "args": args or {},
        "limits": {
            "timeout_s": getattr(settings, "TOOLRUNNER_TIMEOUT", None)
            or getattr(settings, "TOOLRUNNER_TIMEOUT", 30),
            "max_output_bytes": getattr(settings, "TOOLRUNNER_OUTPUT_LIMIT", None)
            or getattr(settings, "TOOLRUNNER_OUTPUT_LIMIT", 4096),
        },
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp, signature = _sign(body, secret)
    headers = {
        "Content-Type": "application/json",
        "X-AM-Timestamp": timestamp,
        "X-AM-Signature": signature,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(base_url, content=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            return {
                "ok": False,
                "result": None,
                "meta": {"timeout_source": "TOOLRUNNER_HTTP_TIMEOUT", "timeout_seconds": timeout},
                "error": f"toolrunner request timed out (source=TOOLRUNNER_HTTP_TIMEOUT timeout_seconds={timeout})",
            }
        except Exception as exc:
            return {"ok": False, "result": None, "meta": {}, "error": str(exc)}

    status = data.get("status")
    exit_code = data.get("exit_code")
    stderr = data.get("stderr") or ""
    result_field = data.get("result") or {}
    if isinstance(result_field, dict) and "tool_result" in result_field:
        effective_result = result_field.get("tool_result")
    else:
        effective_result = result_field

    ok = status == "COMPLETED" and (exit_code is None or exit_code == 0)
    error = None
    if not ok:
        if isinstance(result_field, dict):
            error = result_field.get("error")
        if not error and stderr:
            error = stderr
    return {"ok": ok, "result": effective_result, "meta": data, "error": error}
