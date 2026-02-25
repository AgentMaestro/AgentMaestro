# AgentMaestro Project Checklist

## 0. Repository & Environment Setup

- [x] Local dev environment (Python 3.12 venv)
- [x] Postgres running and accessible
- [x] Redis running and accessible
- [x] Django project scaffolding complete
- [x] Root `.gitignore` created
- [x] Django admin mounted at `/admin/`
- [x] GitHub repo ownership finalized (AgentMaestro identity)
- [x] Branch protection rules configured
- [x] GitHub Actions CI (pytest + lint)
- [x] README badges (build status, license)

---

## 1. Architecture & Contracts

- [x] Architecture defined (Django + Channels + Redis + Postgres)
- [x] WebSocket group topology established:
  - `run.<run_id>`
  - `ws.<workspace_id>`
  - `approvals.<workspace_id>`
- [x] Event contract defined (`event_contracts.py`)
- [x] WebSocket dev testing page created
- [ ] Event topics documented in `docs/events.md`
- [ ] Event versioning strategy defined (optional)

---

## 2. Core Django Models

### Workspace & Identity
- [x] `Workspace`
- [x] `WorkspaceMembership` (roles)

### Agents
- [x] `Agent` model (prompt, config, tool policy)

### Runs & Orchestration
- [x] `AgentRun`
- [x] `AgentStep`
- [x] `RunEvent` (monotonic per-run `seq`)
- [x] `Artifact` (optional)

### Tools
- [x] `ToolDefinition`
- [x] `ToolCall`

### Admin
- [x] Basic model registration
- [ ] Add filters/search to AgentRun admin
- [ ] Add inline Steps + Events under AgentRun
- [ ] Add ToolCall inline view

---

## 3. Real-Time Layer (Channels + Redis)

- [x] ASGI configured
- [x] `WorkspaceConsumer`
- [x] `RunConsumer`
- [x] Redis channel layer integration
- [x] Verified:
  - Single-client delivery
  - Multi-client fanout
- [ ] Add authentication enforcement to WS connections
- [ ] Enforce workspace membership checks
- [ ] Add reconnect logic (`since_seq` support)

---

## 4. Event-Sourcing Core

- [x] `append_event()` with DB-safe sequencing
- [x] `append_event()` uses `transaction.on_commit()` for broadcasting
- [x] Snapshot service implemented
- [x] State transition service implemented
- [x] Tests passing for:
  - Seq increment correctness
  - Persistence correctness
  - WS broadcast correctness
  - No broadcast on rollback
- [ ] Add snapshot serialization contract documentation
- [ ] Add replay harness (reconstruct state from events)

---

## 5. MVP Run Engine (Next Major Milestone)

### Deterministic Tick Loop
- [ ] `runs/services/ticker.py`
- [ ] Celery task `run_tick(run_id)`
- [ ] DB locking strategy on run
- [ ] Minimal state machine:
  - `PENDING → RUNNING`
  - Append stub steps (`MODEL_CALL`, `OBSERVATION`)
  - `RUNNING → COMPLETED`
- [ ] Concurrency tests (no double-advance)

### Start-Run Endpoint + UI
- [ ] POST endpoint to create run
- [ ] Enqueue first tick
- [ ] Run detail page with live WS feed
- [ ] Snapshot on refresh

---

## 6. Approvals Workflow

- [ ] ToolCall creation with `requires_approval`
- [ ] Run transitions to `WAITING_FOR_APPROVAL`
- [ ] Broadcast to `approvals.<workspace_id>`
- [ ] Approval endpoint or WS command
- [ ] Resume run after approval
- [ ] Role-based approval permissions
- [ ] Tests for approval flow

---

## 7. Toolrunner Integration

- [ ] FastAPI toolrunner service
- [ ] Backend → toolrunner secure communication
- [ ] Tool allowlist enforcement
- [ ] Persist tool results to ToolCall
- [ ] Tick consumes tool results
- [ ] Integration tests (stubbed toolrunner)

---

## 8. Sub-Agent / Sub-Run Execution

- [ ] Spawn subrun from parent run
- [ ] Parent run waits (`WAITING_FOR_SUBRUN`)
- [ ] Child completion resumes parent
- [ ] Stream subrun summaries
- [ ] Tests for nested orchestration

---

## 9. Security & Governance

- [ ] Enforce workspace membership on all actions
- [ ] Role-based permission enforcement
- [ ] Approval audit trail
- [ ] Threat model documentation
- [ ] Tool safety documentation

---

## 10. OSS Readiness

- [ ] CONTRIBUTING.md
- [ ] Issue templates
- [ ] Good First Issues labeled
- [ ] Architecture documentation in `/docs`
- [ ] 5-minute quickstart guide
- [ ] CI + coverage badge
- [ ] Versioned milestones (v0.1, v0.2, v0.3)

---

# Suggested Next Implementation Order

1. Deterministic Tick Loop (Celery)
2. Start-Run Endpoint + Live UI
3. Admin Observability Improvements
4. Approvals Workflow
5. Toolrunner Integration
6. Sub-Agent Execution
7. Security Hardening