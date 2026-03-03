Corection# LLM app (AgentMaestro)

This app provides the LLM interface layer used by AgentMaestro’s orchestration loop. It abstracts providers, persists runs/messages/tool calls, and exposes a runner that the orchestrator can call.

## Environment

Required for OpenAI provider:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` (optional)
- `OPENAI_TRANSPORT` (`http` or `ws`, default `http`. `ws` now targets the OpenAI Responses API with incremental tool-calling and session reuse.)
- `OPENAI_WS_TIMEOUT_SECONDS` (default `60`): per-request receive timeout for Responses WebSocket calls.
- `OPENAI_WS_IDLE_TIMEOUT_SECONDS` (default `60`): how long a WS session can idle before the pool closes it.
- `OPENAI_WS_DEBUG` (default `0`): set to `1` or `true` to log payload summaries/events during the WS workflow.

App defaults (can be added to settings or `.env`):

- `LLM_PROVIDER` (default: `openai`)
- `LLM_DEFAULT_PROFILE_PLANNER` (default: `Maestro`)
- `LLM_DEFAULT_PROFILE_CODER` (default: `Apprentice`)
- `LLM_TIMEOUT_SECONDS` (default: `60`)
- `LLM_MAX_RETRIES` (default: `3`)

## Models

- `LLMModelProfile`: DB-configurable model/provider settings per agent (e.g., Maestro/Apprentice).
- `LLMRun`: tracks each LLM execution, provider/model, status, usage.
- `LLMMessage`: ordered message history.
- `LLMToolCall`: captured tool invocations/results.

Register profiles via Django admin (create “Maestro” and “Apprentice” with provider `openai` and the desired model name).

## Runner usage

```python
from llm.services.runner import LLMRunner

runner = LLMRunner()
result = await runner.run(
    prompt="Draft a small plan",
    profile_name="Maestro",
    tools=[...],  # optional OpenAI tool schema list
    orchestration_run_id="...optional...",
)
print(result["text"], result["run_id"])
```

The runner:
- persists the run + messages,
- calls the provider,
- executes requested tools via `toolrunner_bridge.run_tool`,
- loops until no tool calls or `max_tool_rounds` reached,
- returns `{ run_id, text, tool_calls_executed, status, error }`.

## Providers

- `openai` (AsyncOpenAI) — implemented.
- `ollama` — stubbed; implement later in `services/providers/ollama_client.py`.
Add new providers by extending `BaseLLMClient` and registering in `services/registry.py`.

## ToolRunner bridge

`llm/services/toolrunner_bridge.py` defines `run_tool(tool_name, args, orchestration_run_id=None)`. Wire this to the actual ToolRunner service; current implementation raises `NotImplementedError`.

## Streaming

`services/streaming.publish_delta` provides a minimal Channels group sender hook (`group: llm_run_<run_id>`). Not yet wired into the runner.

## Tool schema catalog

`LLMRunner` exposes a canonical schema for each supported tool (repo_tree, search_code, file_read, file_write, file_patch, shell_exec, python_exec) when no `tools` argument is supplied. The schema definitions live in `llm/services/tool_schemas.py` and include the exact property names the provider expects.

Use `python manage.py llm_print_tool_templates` to print each schema together with an example argument dict. Copy the example keys directly into prompts so the bridge does not reject malformed calls (e.g., `file_write` uses `"path"`, not `"filename"`).

## Admin

Admin lists for runs, messages, tool calls, and profiles are registered in `llm/admin.py`.

## Migrations

Run:
```
python manage.py migrate llm
```

## Stage 2 WebSocket tool smoke

Use these commands to exercise the Stage 2 WS tool loop end to end:

1. `powershell -Command "$env:OPENAI_TRANSPORT='ws'"` (or set the equivalent env variable for your shell).
2. *(Optional)* `powershell -Command "$env:OPENAI_WS_DEBUG='1'"` to enable verbose WS payload/event logging.
3. `python manage.py llm_responses_ws_smoke`
4. `python manage.py llm_responses_ws_tool_smoke`

The tool smoke command forces `OPENAI_TRANSPORT=ws`, calls `file_write` then the JSON-producing `shell_exec`, and asserts the run completed with at least two tool calls, final JSON list output, and a recorded `openai_response_id`. Afterward you can drop back to HTTP mode via `powershell -Command "$env:OPENAI_TRANSPORT='http'"` and rerun `python manage.py llm_toolloop_real_smoke` to confirm that transport still works.

## Console stream

Django serves console endpoints at http://127.0.0.1:8000/llm/console/stream (SSE) and http://127.0.0.1:8000/llm/console/detail. The stream polls LLMRun, LLMMessage, and LLMToolCall, builds concise summaries for run events, assistant responses, and tool calls, and emits details_url links for each item. ToolRunner mounts the UI at http://127.0.0.1:8001/ui/console; open it while running python manage.py llm_toolloop_real_smoke to watch events append live. Only the console origin (http://127.0.0.1:8001) may access these endpoints via CORS.
