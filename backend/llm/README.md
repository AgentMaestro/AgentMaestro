Corection# LLM app (AgentMaestro)

This app provides the LLM interface layer used by AgentMaestro’s orchestration loop. It abstracts providers, persists runs/messages/tool calls, and exposes a runner that the orchestrator can call.

## Environment

Required for OpenAI provider:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` (optional)
- `OPENAI_TRANSPORT` (`http` or `ws`, default `http`). HTTP mode uses the Responses API and is the production default; enable websocket mode only for the Stage 2 smoke tests described later.
- `OPENAI_HTTP_MODE` (`responses` or `chat_completions`, default `responses`). Keep this on `responses` so the HTTP and WS transports normalize tool calls the same way.
- `OPENAI_WS_TIMEOUT_SECONDS` (default `60`): per-request receive timeout for Responses WebSocket calls.
- `OPENAI_WS_IDLE_TIMEOUT_SECONDS` (default `60`): how long a WS session can idle before the pool closes it.
- `OPENAI_WS_DEBUG` (default `0`): set to `1` or `true` to log payload summaries/events during the WS workflow.

App defaults (can be added to settings or `.env`):

- `LLM_PROVIDER` (default: `openai`)
- `LLM_DEFAULT_PROFILE_PLANNER` (default: `Maestro`)
- `LLM_DEFAULT_PROFILE_CODER` (default: `Apprentice`)
- `LLM_TIMEOUT_SECONDS` (default: `60`)
- `LLM_MAX_RETRIES` (default: `3`)

## System context

The UI and runner assemble a system prompt via `llm.system_context.build_system_context`. The builder always starts from the same kernel (base operating rules) and layers in a role overlay, runtime facts, and any trimmed `agent.soul` text so the model stays grounded.

- **Role overlay**: The agent's gerund role (`planning`, `coding`, `assisting`, or `researching`) inserts a short directive set (e.g., planning focuses on structured checkpoints while assisting emphasizes concise answers). The overlay nudges reasoning style and tool usage before runtime details.
- **Runtime facts**: A dedicated section reports the model, transport (`http` or `ws`), and up to 12 tool names (with "+N more" when there are additional tools). These lines are presented as mandatory context rather than optional notes.
- **Policy=react**: If `policy_name.lower()` equals `react`, the builder inserts the full ReAct guidance (reason step-by-step, call tools if needed, wait for results, and only reply once the task is complete). Other policy names do not add policy text.
- **AGENTS.md startup rule**: The base kernel now instructs the model to read the repository `AGENTS.md` file at the start of a new session when it is available in context or the workspace, and to begin its first reply by confirming that it has read `AGENTS.md`. The model notice also includes the exact repo-root `AGENTS.md` path so the agent does not incorrectly try a run-local relative path like just `AGENTS.md`.
- **Agent soul**: Any text stored in `agent.soul` is truncated to roughly 450 characters and appended as "Agent-specific instructions," keeping the custom prompt short.

Example output delivered to the model:

```
You are Maestro, an AI agent operating inside the AgentMaestro orchestration platform.
...
Role: coding
- Produce correct, runnable code with minimal surprises.
...
Runtime:
- Model: gpt-5-mini
- Transport: ws
- Tools available: toolrunner.execute, files.read, web.search
```

## Policies

`policy_name` is metadata you store with each agent that points at an `LLMModelProfile`. The UI shows `Policy: react` by default; when that policy is selected, the system context builder injects the full ReAct policy text described above. Other policy names still influence the model, temperature, and extras defined in their profile but do not append policy text. Policy definitions live in `backend/llm/policy.MD` (ReAct, Planner, Coder, etc.), and the system context explicitly tells the model where to find the file when a run starts.

Common policies are simply the names of the profiles you seed in Django admin:

- `react` (captures the ReAct guidance: think step-by-step, call tools when needed, wait for responses, and only reply once the task is complete).
- `planner`, `coder`, `maestro`, `apprentice`, etc. - each points at a different `LLMModelProfile` (model, temperature, extras) and may represent a different reasoning or tool usage style. Create profiles for the behavior you need and reference them via `policy_name`.

You can add new policies by creating `LLMModelProfile` rows with the desired name and settings; updating the agent's `policy_name` will cause the new instructions and model to be used on the next run.

## Models

- Default agents can only select Responses API-compatible models: `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-reasoning`, `gpt-5-reasoning-mini`, `gpt-5-code`, `gpt-4.1`, `gpt-4.1-mini`, or `gpt-4.1-nano`.

- `LLMModelProfile`: DB-configurable model/provider settings per agent (e.g., Maestro/Apprentice).
- `LLMRun`: tracks each LLM execution, provider/model, status, usage.
- `LLMMessage`: ordered message history.
- `LLMToolCall`: captured tool invocations/results.

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

`LLMRunner` exposes a fallback schema catalog when no `tools` argument is supplied. The fallback catalog now mirrors the executable released tools from `backend/tools/registry.py`, with descriptions/examples added in `llm/services/tool_schemas.py`.

Current fallback tool list:

- `repo_tree`
- `search_code`
- `file_read`
- `file_write`
- `file_delete`
- `file_patch`
- `shell_exec`
- `python_exec`
- `git_add`
- `git_status`
- `git_diff`
- `git_log`
- `git_apply`
- `git_branch_create`
- `git_checkout`
- `git_push`
- `run_command`
- `test_runner`
- `format_runner`
- `coverage_runner`
- `lint_runner`
- `typecheck_runner`

`webhook` is cataloged separately but is not part of the fallback `get_tool_schemas()` list yet because it still uses the dedicated ToolRunner webhook endpoint rather than the normal `/v1/run/tool` dispatch path.

Use `python manage.py llm_print_tool_templates` to print each schema together with an example argument dict. Copy the example keys directly into prompts so the bridge does not reject malformed calls (e.g., `file_write` uses `"path"`, not `"filename"`).

## Admin

Admin lists for runs, messages, tool calls, and profiles are registered in `llm/admin.py`.

## Migrations

Run:
```
python manage.py migrate llm
```

## Stage 2 WebSocket tool smoke

The LLM app now runs against the HTTP/Responses transport by default, but these commands allow you to exercise the older websocket-based Stage 2 smoke path when you need to verify incremental tool-calling or session reuse.

1. `powershell -Command "$env:OPENAI_TRANSPORT='ws'"` (or set the equivalent env variable for your shell).
2. *(Optional)* `powershell -Command "$env:OPENAI_WS_DEBUG='1'"` to enable verbose WS payload/event logging.
3. `python manage.py llm_responses_ws_smoke`
4. `python manage.py llm_responses_ws_tool_smoke`

The tool smoke command forces `OPENAI_TRANSPORT=ws`, calls `file_write` then the JSON-producing `shell_exec`, and asserts the run completed with at least two tool calls, final JSON list output, and a recorded `openai_response_id`. Afterward reset the defaults (`OPENAI_TRANSPORT='http'` and `OPENAI_HTTP_MODE='responses'`) and rerun `python manage.py llm_toolloop_real_smoke` to confirm the HTTP mode still behaves as expected.

## Console stream

Django serves console endpoints at http://127.0.0.1:8000/llm/console/stream (SSE) and http://127.0.0.1:8000/llm/console/detail. The stream polls LLMRun, LLMMessage, and LLMToolCall, builds concise summaries for run events, assistant responses, and tool calls, and emits details_url links for each item. ToolRunner mounts the UI at http://127.0.0.1:8001/ui/console; open it while running python manage.py llm_toolloop_real_smoke to watch events append live. Only the console origin (http://127.0.0.1:8001) may access these endpoints via CORS.
