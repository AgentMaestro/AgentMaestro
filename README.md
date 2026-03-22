# AgentMaestro

**AgentMaestro** is a state-machine-driven agent orchestration framework
built with Django and Channels.

It coordinates multi-agent workflows through explicit state transitions,
durable execution history, and approval-based tool control.

AgentMaestro separates probabilistic reasoning from deterministic
orchestration --- making AI systems transparent, resumable, auditable,
and production-safe.

------------------------------------------------------------------------

# Current Capabilities (Mar 22, 2026)

- Full agent runtime with event loop
- ToolRunner execution system (async-ready)
- OpenAI WS + HTTP integration
- Gemini HTTP-compatible provider path
- Provider/system transport logs now support condensed or raw JSON mode
- Multi-agent switching capability
- Full developer and researcher tool suite
- Code navigation tools for fast workspace search and symbol lookup: `search_files`, `list_symbols`, `find_symbol`, `find_references`, and `jump_to_symbol`
- `search_files` is path/name-only, not content search; use `search_code` for text or regex content searches. Search one path/name query at a time; use separate calls for unrelated targets, and use `|` only inside `is_regex=true` queries
- Code navigation tools support `compact=true` for a smaller standardized `items` payload, and they should be used sequentially one call at a time when precision matters
- `search_code` remains the content search tool, with regex alternation via `|` when `is_regex=true`
- Google Bridge Gmail and Calendar read/write support, including top-level Gmail query OR splitting for list/read and bulk trash/delete, with a 10-clause default cap and account_scope=all fan-out
- Native `google_bridge` tool for Gmail/Calendar reads, Gmail draft/send/trash/delete, and Calendar create/update/delete workflows
- `get_current_datetime` utility for ISO 8601 local time in the Tango timezone
- Initial system context seeds the current local datetime and timezone for relative-date resolution
- Relative dates like "tomorrow" use the local Tango timezone from `TIME_ZONE` / `settings.TIME_ZONE` (an IANA timezone name such as `America/New_York`)
- Scheduling and Calendar date arguments default to the same local Tango timezone when an explicit timezone is omitted
- Run timeline debug page for AgentStep inspection
- Safe bounded command execution and repo-scripted test runners
- Telegram communication channel
- Memory (STM + LTM)
- Scheduled tasks with headless execution
- Autonomous coding workflows

----------------------------------------------------------------------------------------------------

## Why AgentMaestro?

Many AI agent frameworks rely on implicit loops and in-memory control
flow:

``` python
while not done:
    call_model()
    if tool_requested:
        run_tool()
```

This approach makes systems difficult to:

-   Inspect
-   Resume
-   Audit
-   Scale safely
-   Run in multi-tenant environments

AgentMaestro takes a different approach.

Every agent run progresses through persisted states with well-defined
transitions governed by a deterministic decision table. All steps are
recorded. All tool calls are tracked. All transitions are explicit.

This design enables:

-   Reliable concurrency
-   Safe cancellation
-   Approval-gated execution
-   Event replay
-   Clear debugging
-   Sub-agent orchestration
-   Production-grade multi-user control
-   Diagnostic tools that are used to improve agent orchestration and efficiency

------------------------------------------------------------------------

## Core Design Principles

### 1. Deterministic State Machine Orchestration

Each run moves through explicit states:

    PENDING → RUNNING → WAITING_FOR_APPROVAL → RUNNING → COMPLETED

Transitions are controlled by a clear decision table—not hidden
recursion.

------------------------------------------------------------------------

### 2. Multi-Tenant Isolation

All agents, runs, and tool calls belong to a workspace.

Designed for:

-   Multiple users
-   Multiple agents
-   Shared environments
-   Clear permission boundaries

------------------------------------------------------------------------

### 3. Approval-Gated Tool Execution

Tool execution is explicit and auditable.

Risk levels:

-   SAFE
-   ELEVATED
-   DANGEROUS

Dangerous actions can require manual approval before execution.

------------------------------------------------------------------------

### 4. Event-Sourced Run History

Every significant action generates a `RunEvent`.

-   Monotonic sequence numbers
-   WebSocket streaming
-   Replayable history
-   Live observability

------------------------------------------------------------------------

### 5. Sub-Agent Lifecycle Management

Runs can spawn sub-runs.

Parent/child relationships are first-class, enabling:

-   Hierarchical workflows
-   Coordinated multi-agent systems
-   Controlled delegation

------------------------------------------------------------------------

## Quick Start

1.  `cd backend`, copy `.env.example` to `.env`, and populate the required secrets (`SECRET_KEY`, `DATABASE_URL`, `CELERY_BROKER_URL`, `CHANNEL_LAYER_REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, etc.).
2.  Create a virtual environment (`python -m venv .venv`), activate it, and install dependencies (`.venv\\Scripts\\pip install -r requirements.txt`).
3.  Run `python manage.py migrate`.
4.  Seed the tool catalog: `python manage.py seed_tools`.
5.  Seed the workspace tool shelf: `python manage.py seed_workspace_tools --workspace 'Dev Workspace' --enable-all` (add `--include-unreleased` if you need unreleased tools).
6.  Launch Redis, then start Celery worker + beat in separate consoles:
    - `celery -A agentmaestro worker --loglevel=info --pool=solo`
    - `celery -A agentmaestro beat --loglevel=info`
7.  Run Django: `python manage.py runserver`.
8.  Visit `/agents/new` to configure agents, `/agents/<agent_slug>/` for the chat UI, and `/ui/agents/<uuid>/connect/telegram/` to pair bots.
9.  Use `backend/scripts/runtests.ps1` to execute the backend test suite safely on Windows.
10. Use `toolrunner/scripts/runtests.ps1` to execute the toolrunner test suite safely on Windows.

## Architecture Overview

    Browser (UI)  --> WebSockets --> Django (ASGI + Channels)
                                     |
                                     v
                               Celery + Redis
                                     |
                                     v
                             Agent Orchestrator
                                     |
                                     v
                               FastAPI Tool Runner

### Security and tool governance

- **Workspace isolation:** each Agent, AgentRun, and ToolCall belongs to a workspace that owns ToolDefinitions. Only enabled ToolDefinitions may appear in the wizard or be granted to agents.
- **Tool policy hierarchy:** workspace shelves control which tools are visible, released gating keeps unreleased functionality locked for non-superusers, and AgentToolGrant enforces explicit enablement (default-deny) before an AI can invoke a tool.
- **Risk tiers & approvals:** tools are classified as SAFE, ELEVATED, or DANGEROUS. Elevated/Dangerous calls create ToolCalls with status PENDING_APPROVAL; manual approval (via the WS approval card) progresses them to QUEUED/RUNNING while denials emit audit-friendly observations back to the provider.
- **Bounded developer execution:** `run_command_safe` is the no-approval path for tightly allowlisted developer commands inside the workspace. Allowed executables are limited to `python`, `pytest`, `ruff`, `mypy`, `uv`, and `django-admin`. Git is excluded from that surface and must go through the dedicated `git_*` tools. Common rejected examples include `git status`, `pip install`, `python manage.py runserver`, and chained shell commands. `run_tests` is limited to the repo-owned PowerShell test entrypoints.
- **Async Celery execution:** approved tool calls enqueue `tools.execute_tool_call_async`, register `celery_task_id`, and persist the result or error payload. Completion events publish via the channel layer so the WebSocket consumer can resume the OpenAI session with the tool output without polling.
- **Persisted observability:** every user/assistant/tool turn is stored as `LLMMessage` linked to `AgentRun` <-> `LLMRun`. The `/agents/<slug>/` page replays the last 50 turns, while RunEvents and ToolCalls provide a durable audit trail.
- **Pairing & transport security:** pairing codes bind chat IDs to endpoints; Telegram adapters accept `/pair <code>` only from allowlisted users, and transports remain dormant until a valid pairing claims the conversation. Secrets (bot tokens, chat IDs, Celery credentials) stay in `.env` and are never logged.

### Removing a tool from the system

If a tool should remain internal-only or be removed from agent prompts entirely, remove it from every layer below:

1. Remove the tool from `backend/tools/registry.py`.
2. Remove the shared `Tool` row in admin.
3. Remove or disable the workspace `ToolDefinition` rows for that tool.
4. Remove or disable the `AgentToolGrant` rows for that tool.
5. Remove the tool name from any `Agent.tool_policy_json["selected_tools"]` lists so agent metadata matches reality.

Notes:

- The effective tool set sent to the model comes from `ToolDefinition` + `AgentToolGrant` + release gating, not from `tool_policy_json` alone.
- Removing a tool name from `tool_policy_json` and saving the agent does not by itself revoke access.
- If the tool remains in `backend/tools/registry.py`, future `seed_tools` / `seed_workspace_tools` runs can recreate it.

### Adding a new tool

Use this checklist when introducing a new tool:

1. Add the canonical tool entry to `backend/tools/registry.py` with the correct schema, risk, approval default, and release flag.
2. Implement the ToolRunner handler in `toolrunner/app/tools/`, add the matching args model in `toolrunner/app/models.py`, and register it in `toolrunner/app/main.py`.
3. Run `python manage.py seed_tools` so the shared `Tool` row exists.
4. Run `python manage.py seed_workspace_tools --workspace <workspace>` or create the workspace `ToolDefinition` manually, making sure it is linked to the shared `Tool`.
5. Create or enable the `AgentToolGrant` row for each agent that should actually receive the tool.
6. Note, for dev purposes 3, 4, and 5 can be performed by running `python manage.py seed_dev_tools` which will add to `Tools`, `ToolDefinitions`, and grant to all Agents in `AgentToolGrant`.
7. Verify the risk and approval defaults on both the shared `Tool` and any workspace override in `ToolDefinition`.
8. Optionally add the tool name to `Agent.tool_policy_json[\"selected_tools\"]` for metadata consistency, but do not rely on that JSON alone for actual access.

### Observability

- WebSocket consumers stream `RunEvent`/`LLMMessage` updates, show approval cards, and emit tool status events in the chat UI.
- Celery logs surface Telegram polling retries (timeout default 25s) and tool execution progress.
- `backend/scripts/runtests.ps1` handles Windows permission quirks by clearing `.pytest-temp` and passing `--basetemp`.

### Components

-   **Django** --- Control plane, state persistence, workspace
    management
-   **Channels** --- Real-time WebSocket streaming
-   **PostgreSQL** --- Durable run + step history, agent memory
-   **Celery + Redis** --- Background orchestration ticks
-   **FastAPI Tool Runner** --- Sandboxed tool execution

------------------------------------------------------------------------

## Run Lifecycle

Each run progresses through atomic "ticks":

1.  `MODEL_CALL` step
2.  `TOOL_CALL` step (if requested)
3.  `OBSERVATION` step
4.  `MODEL_CALL` (repeat)
5.  `MESSAGE` step (completion)

Every step is stored. Every transition is persisted.

This allows:

-   Safe crash recovery
-   Resume from mid-execution
-   Deterministic debugging
-   Concurrency control

------------------------------------------------------------------------

## Current Status

🚧 Early Development

### Planned Milestones

-   [ ] Workspace + multi-tenant foundation
-   [ ] Deterministic run engine
-   [ ] Tool registry and approval workflow
-   [ ] Sub-agent orchestration
-   [ ] Telegram integration
-   [ ] Budget enforcement and quotas
-   [ ] Observability dashboard

------------------------------------------------------------------------

## Project Philosophy

AgentMaestro is built on a simple belief:

> AI reasoning may be probabilistic, but orchestration should be deterministic.

Control flow should be explicit.
Execution should be inspectable.
State should be durable.
Tools should be governed.

------------------------------------------------------------------------

## Getting Started (Early Scaffold)

> Setup instructions will be expanded as core infrastructure stabilizes.

Planned stack:

-   Python 3.11+
-   Django 4.x+
-   Channels
-   Redis
-   PostgreSQL
-   FastAPI

------------------------------------------------------------------------

## Telegram Control Surface

- `TELEGRAM_BOT_TOKEN`: the bot token the polling worker uses to talk to Telegram.
- `TELEGRAM_POLL_INTERVAL_SECONDS`: how frequently the scheduler enqueues `comms.telegram_poll_scheduler` (defaults to 5s).
- `TELEGRAM_POLL_TIMEOUT_SECONDS`: the fixed long-poll timeout we pass to Telegram (default 5s).
- `TELEGRAM_POLL_LOCK_REDIS_URL` / `CHANNEL_LAYER_REDIS_URL` / `CELERY_BROKER_URL`: Redis connections used for channel layers, polling locks, and the Celery broker.

### Running the poller

Run the worker and beat to stream Telegram updates into Control:

```
celery -A agentmaestro worker -l info
celery -A agentmaestro beat -l info
```

Once Celery is running, the scheduler enqueues `comms.telegram_poll_scheduler` which drives `comms.tasks.telegram_poll_once`. The new mirror UI is available at `/ui/chat/` (and the Telegram filter at `/ui/comms/telegram/`).

------------------------------------------------------------------------

## Contributing

AgentMaestro is designed as a long-term, open, infrastructure project.

We welcome contributions in:

-   Orchestration logic
-   Tool integrations
-   Concurrency improvements
-   Documentation
-   UI enhancements
-   Security reviews

Please read `CONTRIBUTING.md` before submitting a PR.

------------------------------------------------------------------------

## License

AgentMaestro is licensed under the Apache License 2.0.

See `LICENSE` for details.

------------------------------------------------------------------------

## Vision

The long-term goal of AgentMaestro is to provide:

-   A transparent alternative to opaque agent frameworks
-   A safe foundation for multi-agent systems
-   A deterministic orchestration layer for AI systems
-   A production-grade control plane for tool-using agents

------------------------------------------------------------------------

# Roadmap

## Short Term

-   Deterministic run engine
-   WebSocket live streaming
-   Tool approval workflow

## Medium Term

-   Sub-agent tree visualization
-   Quotas + budgeting
-   External integrations (Telegram, API)

## Long Term

-   Plugin ecosystem
-   Observability tooling
-   Production deployment patterns
-   Hosted orchestration layer

------------------------------------------------------------------------

# Final Thought

AgentMaestro is not just an AI agent framework.  It is an orchestration and tool approval engine that puts you in control.  Choose what files to share.  Choose what tools to allow.  Choose what the AI can do on your system and make the AI more efficient.

