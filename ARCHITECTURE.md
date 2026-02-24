# AgentMaestro Architecture

AgentMaestro is a state-machine-driven orchestration engine built on
Django, Channels, Celery, and PostgreSQL.

------------------------------------------------------------------------

## High-Level Architecture

Browser (UI) → Django (ASGI + Channels) │ │ Celery + Redis ▼ Agent
Orchestrator │ ▼ FastAPI Tool Runner

------------------------------------------------------------------------

## Core Components

### 1. Control Plane (Django)

-   Workspace isolation
-   Agent definitions
-   Run persistence
-   Step tracking
-   Approval workflow

### 2. Orchestrator (Celery Workers)

-   Deterministic tick-based progression
-   State transitions governed by decision table
-   Sub-run spawning
-   Budget enforcement

### 3. Event Layer (Channels)

-   WebSocket streaming
-   RunEvent persistence
-   Workspace and user group broadcasting

### 4. Tool Runner (FastAPI)

-   Sandboxed execution
-   Allowlisted filesystem and shell commands
-   Risk-based execution model
-   Approval-gated execution

------------------------------------------------------------------------

## Run Lifecycle

Each run progresses via atomic ticks:

1.  MODEL_CALL
2.  TOOL_CALL
3.  OBSERVATION
4.  MODEL_CALL
5.  MESSAGE

All transitions are explicit and persisted.

------------------------------------------------------------------------

## Multi-Tenancy Model

All data is scoped to a Workspace:

-   Agents
-   Runs
-   ToolCalls
-   Artifacts

Permissions are enforced at the workspace boundary.

------------------------------------------------------------------------

## Sub-Agent Model

Runs may spawn sub-runs:

-   Parent-child relationships are first-class.
-   Sub-runs are fully independent state machines.
-   Parent may wait or continue depending on orchestration policy.

------------------------------------------------------------------------

## Design Goals

-   Deterministic control
-   Explicit transitions
-   Safe concurrency
-   Auditability
-   Production-grade architecture

