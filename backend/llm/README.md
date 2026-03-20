Corection# LLM app (AgentMaestro)

This app provides the LLM interface layer used by AgentMaestro’s orchestration loop. It abstracts providers, persists runs/messages/tool calls, and exposes a runner that the orchestrator can call.

## Environment

Required for OpenAI provider:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` (optional)
- `OPENAI_TRANSPORT` (`ws` or `http`, default `ws`). WebSocket transport is the default path; switch to HTTP Responses mode when you need a diagnostic fallback or clearer validation errors.
- `OPENAI_HTTP_MODE` (`responses` or `chat_completions`, default `responses`). Keep this on `responses` so the HTTP and WS transports normalize tool calls the same way.
- `OPENAI_WS_TIMEOUT_SECONDS` (default `60`): per-request receive timeout for Responses WebSocket calls.
- `OPENAI_WS_IDLE_TIMEOUT_SECONDS` (default `60`): how long a WS session can idle before the pool closes it.
- `OPENAI_WS_DEBUG` (default `0`): set to `1` or `true` to log payload summaries/events during the WS workflow.

Required for Gemini provider:

- `GEMINI_API_KEY`
- `GEMINI_BASE_URL` (optional, default `https://generativelanguage.googleapis.com/v1beta`)
- `GEMINI_TRANSPORT` (default `http`). Non-HTTP values are coerced back to HTTP for now because Gemini WS/Live transport is not implemented in this sprint.
- `SHOW_CONDENSED_SYSTEM_LOGS` (`1` or `0`, default `1`). `1` shows condensed system logs; `0` shows full payloads and raw responses in the system stream.

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

## Transport Protocols

The provider-specific message and tool flow is documented in [`PROTOCOLS.md`](PROTOCOLS.md).

Read that file when you need the exact answers to:

- what each provider receives in its initial prompt,
- how tool schemas are passed,
- how tool calls are returned,
- how tool results are serialized back into the next turn,
- and how OpenAI WS, OpenAI HTTP Responses, OpenAI HTTP Chat Completions, and Gemini HTTP differ.

The system chat stream also follows `SHOW_CONDENSED_SYSTEM_LOGS`, so the logs either show the raw JSON payloads or condensed summaries with an admin link to the corresponding `AgentStep`.

## AgentChatConsumer Neutrality

`AgentChatConsumer` should stay provider-neutral. Its job is to orchestrate the run, preserve local history, and resume the conversation after tools or reconnects. It should not encode provider-specific business logic.

Keep these boundaries intact:

- The consumer owns run state, local history, bootstrap state, approvals, and tool result flow.
- Provider-specific quirks belong in the provider adapters under `services/providers/`.
- Transport-specific serialization belongs in the input-item builders and provider clients, not in the consumer.
- The consumer should work from normalized concepts: `provider`, `model_name`, `transport`, `previous_response_id`, `tool_calls`, and local `history`.

Normalization rules used by the current implementation:

- `agents.utils.normalize_provider_for_model()` resolves the provider from the selected model when the profile and model disagree.
- The consumer keeps one canonical run history and uses `_current_previous_response_id()` to preserve continuity where the provider supports it.
- `runs.services.input_items.build_input_items()` and `build_ws_request_input_items()` translate the shared local history into provider-specific wire formats.
- OpenAI uses `previous_response_id` for continuity; Gemini rebuilds from local history and normalized contents instead of an OpenAI-style chain token.
- Bootstrap and memory state are run-level concerns, so they should be persisted once and replayed consistently regardless of provider or transport.

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

### Agent `default_model` dropdown

The Agent admin and the Agent LLM form both populate `default_model` from `Agent.get_default_model_choices()`.

That method reads `llm.ModelsAvailable` and only includes rows that match one of these provider/API pairs:

- `company = openai`, `api = responses`
- `company = google`, `api = gemini`

So if you add a new Gemini model and want it to appear in the Agent `default_model` dropdown, the `ModelsAvailable` row must use:

- `company = google`
- `api = gemini`
- `name = <exact model id>`

If the row does not match those fields exactly, the model will not appear in the dropdown and the agent may normalize back to the default model during save/load.

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

## Bridge tests

`backend/llm/tests/test_toolrunner_bridge.py` is an opt-in integration test suite for the backend-to-ToolRunner bridge.

- Set `RUN_TOOLRUNNER_BRIDGE_TESTS=1` to enable it.
- Leave it unset, `0`, or any non-truthy value to keep the suite skipped.
- The default is off when the variable is missing.
- The test module reads `os.environ` directly, so this flag is a test-only gate rather than a Django settings value.
- The suite runs through the backend test runner (`backend/scripts/runtests.ps1`), not the ToolRunner test runner.
- Use it when you want an end-to-end backend-to-ToolRunner verification of the bridge path.

## Provider matrix smoke

`python manage.py llm_provider_matrix_smoke` runs the default provider/model/transport benchmark matrix from `smoke/llm_provider_matrix.json`.

The default scenarios are:

- `direct_reply`
- `repo_lookup`

The command reports:

- `speed_score`
- `quality_score`
- `overall_score`
- `grade`

Use `--output <path>` when you want a saved JSON report.

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

The LLM app now runs against the WebSocket transport by default, while HTTP/Responses remains the backup path when you need a simpler diagnostic transport or clearer OpenAI validation errors.

1. `powershell -Command "$env:OPENAI_TRANSPORT='ws'"` (or set the equivalent env variable for your shell).
2. *(Optional)* `powershell -Command "$env:OPENAI_WS_DEBUG='1'"` to enable verbose WS payload/event logging.
3. `python manage.py llm_responses_ws_smoke`
4. `python manage.py llm_responses_ws_tool_smoke`

The tool smoke command forces `OPENAI_TRANSPORT=ws`, calls `file_write` then the JSON-producing `shell_exec`, and asserts the run completed with at least two tool calls, final JSON list output, and a recorded `openai_response_id`. Afterward, if you need a backup diagnostic pass, switch to `OPENAI_TRANSPORT='http'` with `OPENAI_HTTP_MODE='responses'` and rerun `python manage.py llm_toolloop_real_smoke` to confirm the HTTP path still behaves as expected.

## Quick tool smoke

Use short prompts and stop after the first failure. A compact five-tool pass that covers the most-used surfaces:

1. `file_read`: "Read `AGENTS.md`. Reply: read ok."
2. `git_status`: "Run `git_status` in the repo root. Reply with branch and clean/dirty only."
3. `search_code`: "Find `OPENAI_TRANSPORT`. Reply with the file path only."
4. `remember`: "Remember: keep answers short. Reply: saved."
5. `run_tests`: "Run the smallest safe smoke test. Reply pass/fail only."

## Console stream

Django serves console endpoints at http://127.0.0.1:8000/llm/console/stream (SSE) and http://127.0.0.1:8000/llm/console/detail. The stream polls LLMRun, LLMMessage, and LLMToolCall, builds concise summaries for run events, assistant responses, and tool calls, and emits details_url links for each item. ToolRunner mounts the UI at http://127.0.0.1:8001/ui/console; open it while running python manage.py llm_toolloop_real_smoke to watch events append live. Only the console origin (http://127.0.0.1:8001) may access these endpoints via CORS.
