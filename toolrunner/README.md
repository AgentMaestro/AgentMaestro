# ToolRunner service

This FastAPI service executes ToolRunner tool calls for AgentMaestro runs.

## Endpoints

- `POST /v1/run/tool`
  - Execute a single tool call for a run/workspace.
  - Requires signed JSON (see `auth.py` for `X-AM-Timestamp`/`X-AM-Signature`).
  - Accepts `ExecuteRequest` payload and returns `ExecuteResponse`.
- `POST /v1/run/tool/webhook`
  - Ingest webhook events (may enqueue tool runs or create resources).
  - Also signed; payload forwarded to `create_webhook`.
- Legacy aliases `/v1/execute` and `/v1/webhook` still exist while clients migrate.

## Tool list

Supported `tool_name` values are restricted via Pydantic and dispatcher:

- `shell_exec`, `python_exec`
- `file_read`, `file_write`
- `repo_tree`, `search_code`

Any other values fail validation or raise `ValueError`.

## Signature

Requests must include:

- `X-AM-Timestamp`: UNIX epoch seconds (within 60s skew)
- `X-AM-Signature`: `HMAC_SHA256(secret, f"{timestamp}.{body}")`

Use `TOOLRUNNER_SECRET` (default `insecure-secret`) to compute/verify.

## Developer notes

- Tool dispatch lives in `app/main.py`.
- Helpers & schemas live in `app/models.py`.
- Auth uses `app/auth.py` and `config.py`.
