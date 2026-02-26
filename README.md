# AgentMaestro

**AgentMaestro** is a state-machine-driven agent orchestration framework
built with Django and Channels.

It coordinates multi-agent workflows through explicit state transitions,
durable execution history, and approval-based tool control.

AgentMaestro separates probabilistic reasoning from deterministic
orchestration --- making AI systems transparent, resumable, auditable,
and production-safe.

------------------------------------------------------------------------

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

## Architecture Overview

    Browser (UI)  ── WebSockets ──> Django (ASGI + Channels)
                                             │
                                             │ Celery + Redis
                                             ▼
                                     Agent Orchestrator
                                             │
                                             ▼
                                     FastAPI Tool Runner

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

AgentMaestro is not just an AI agent framework.

It is an orchestration engine.

