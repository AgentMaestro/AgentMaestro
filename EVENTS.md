# Event Topics & Contracts

## Overview

AgentMaestro uses an event-sourced orchestration model.

All state changes and significant runtime actions are persisted as
`RunEvent` records and optionally broadcast over WebSocket channels.

Events are:

-   Durable (persisted in Postgres)
-   Monotonically sequenced per run
-   Broadcast only after DB commit
-   Replay-safe
-   Structured and versionable

The database is the source of truth. WebSocket streaming is a delivery
mechanism only.

------------------------------------------------------------------------

# Event Envelope

All WebSocket push messages use the following envelope:

``` json
{
  "type": "push",
  "topic": "run.event | workspace.event | approvals.event",
  "ts": "ISO-8601 timestamp",
  "event": "event_name",
  "data": { ... },
  "seq": 42,
  "run_id": "uuid (optional)",
  "workspace_id": "uuid (optional)"
}
```

## Field Definitions

  Field            Required   Description
  ---------------- ---------- -----------------------------------
  `type`           Yes        Always `"push"`
  `topic`          Yes        Logical event stream
  `ts`             Yes        ISO timestamp of event creation
  `event`          Yes        Event name
  `data`           Yes        Event-specific payload
  `seq`            Optional   Monotonic per-run sequence number
  `run_id`         Optional   UUID of run
  `workspace_id`   Optional   UUID of workspace

------------------------------------------------------------------------

# Topics

## 1. `run.event`

Scope: Single run\
Group: `run.<run_id>`

Used for:

-   State transitions
-   Step creation
-   Tool call updates
-   Artifacts
-   Execution milestones

### Example Events

#### `state_changed`

``` json
{
  "event": "state_changed",
  "data": {
    "from": "PENDING",
    "to": "RUNNING"
  }
}
```

#### `step_created`

``` json
{
  "event": "step_created",
  "data": {
    "step_index": 1,
    "kind": "MODEL_CALL",
    "payload": {...}
  }
}
```

#### `tool_call_created`

``` json
{
  "event": "tool_call_created",
  "data": {
    "tool_call_id": "uuid",
    "tool_name": "web_search",
    "requires_approval": true
  }
}
```

#### `tool_call_completed`

``` json
{
  "event": "tool_call_completed",
  "data": {
    "tool_call_id": "uuid",
    "status": "COMPLETED",
    "result_summary": "..."
  }
}
```

#### `artifact_created`

``` json
{
  "event": "artifact_created",
  "data": {
    "artifact_id": "uuid",
    "type": "file",
    "name": "report.pdf"
  }
}
```

------------------------------------------------------------------------

## 2. `workspace.event`

Scope: Entire workspace\
Group: `ws.<workspace_id>`

Used for:

-   Run lifecycle notifications
-   Workspace-level visibility
-   High-level status changes

### Example Events

#### `run_started`

``` json
{
  "event": "run_started",
  "data": {
    "run_id": "uuid",
    "agent_name": "ResearchAgent"
  }
}
```

#### `run_completed`

``` json
{
  "event": "run_completed",
  "data": {
    "run_id": "uuid",
    "status": "COMPLETED"
  }
}
```

#### `run_failed`

``` json
{
  "event": "run_failed",
  "data": {
    "run_id": "uuid",
    "reason": "Tool execution error"
  }
}
```

------------------------------------------------------------------------

## 3. `approvals.event`

Scope: Workspace approvals\
Group: `approvals.<workspace_id>`

Used for:

-   Tool approval requests
-   Approval status updates

### Example Events

#### `approval_requested`

``` json
{
  "event": "approval_requested",
  "data": {
    "tool_call_id": "uuid",
    "run_id": "uuid",
    "tool_name": "file_write",
    "reason": "Potentially destructive operation"
  }
}
```

#### `approval_granted`

``` json
{
  "event": "approval_granted",
  "data": {
    "tool_call_id": "uuid",
    "approved_by": "user_id"
  }
}
```

#### `approval_rejected`

``` json
{
  "event": "approval_rejected",
  "data": {
    "tool_call_id": "uuid",
    "rejected_by": "user_id",
    "reason": "Insufficient context"
  }
}
```

------------------------------------------------------------------------

# Persistence Model

All events in `run.event` must:

-   Be persisted as `RunEvent`
-   Have a strictly increasing `seq`
-   Be broadcast using `transaction.on_commit`

Workspace and approval events may:

-   Derive from run events
-   Or be explicitly appended

Replay safety requires:

-   No event may be broadcast if the transaction rolls back
-   `seq` must be unique per run
-   Snapshot reconstruction must use ordered `seq`

------------------------------------------------------------------------

# Replay & Snapshot Semantics

Clients may reconnect with:

    since_seq = N

Server must:

-   Return ordered events where `seq > N`
-   Or return full snapshot if `since_seq` is null

Ordering guarantee:

Events must be delivered in strictly increasing `seq` order.

------------------------------------------------------------------------

# Versioning Strategy (Future)

If event payload structure changes:

-   Add `event_version` field to `data`
-   Or version event names (e.g., `step_created.v2`)
-   Never mutate historical event shapes

Event immutability is required for deterministic replay.

------------------------------------------------------------------------

# Design Principles

1.  Database is canonical.
2.  Events are immutable.
3.  Broadcast occurs only after commit.
4.  Replay must produce identical derived state.
5.  Topics represent visibility scope.
6.  Consumers must not mutate authoritative state.
