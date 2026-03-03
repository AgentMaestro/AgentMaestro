## Agent Flows User Testing Plan (Stages 4–6)

### 1. Prerequisites
- Windows development machine with Python 3.12 virtual environment activated.
- PostgreSQL database configured per `.env` / `agentmaestro/settings/base.py`.
- Redis instance reachable at `redis://127.0.0.1:6379/0` for Celery and Channels.
- Celery broker/settings (`CELERY_BROKER_URL`, `CHANNEL_LAYER_REDIS_URL`) pointing to Redis.
- Telegram transport configured (optional for transport CTA verification).

### 2. Setup Instructions
1. Apply migrations/seeds:
   ```powershell
   cd backend
   .venv\Scripts\python.exe manage.py migrate
   .venv\Scripts\python.exe manage.py seed_tools
   ```
   - Use `--include-unreleased` if you also want the unreleased (Tier 2) tools added to the catalog for Dev workspaces.
2. Run Redis (Windows build or Docker image) and export URLs:
   ```powershell
   redis-server --port 6379
   ```
3. Start Django dev server:
   ```powershell
   .venv\Scripts\python.exe manage.py runserver
   ```
4. Start Celery worker (solo pool recommended on Windows):
   ```powershell
   .venv\Scripts\python.exe -m celery -A agentmaestro worker --loglevel=info --pool=solo
   ```
5. Optional: load Telegram transport (bot token/chat id) via admin or env for transport CTA testing (see `.env.example`).

### 3. Manual Test Cases

#### A. Agent Creation Wizard (/agents/new)
- Steps: Visit `/agents/new`, go through Basics → LLM → Workspace → Tools → Review.
- Actions:
  - Input name, description, soul, owner.
  - Pick policy/model, temperature, plan toggle.
  - Choose Workspace (existing or create new) and select tools via grouped toggles.
  - Review summary and finish.
- Expected:
  - Slug generated, owner assigned.
  - Redirect to `/agents/<slug>/`.
  - Agent detail lists workspace, tool count, transport CTA.

#### B. Authentication / Access Control
- Steps: log in as owner or workspace member (admin creates users via Django admin or `create_superuser` / `createsuperuser`).
- Actions:
  - Confirm `/agents/<slug>/` accessible to owner/workspace member.
  - Confirm unauthorized user is redirected or `PermissionDenied`.
- Expected:
  - Only allowed users reach detail view; unauthorized requests show 403.

#### C. Chat Persistence (/agents/<slug>/ + /ws/agents/<slug>/chat/)
- Steps: open agent detail, verify last 50 turns render, chat messages persisted.
- Actions:
  - Send user messages via UI; watch websocket connect/status.
  - Reload page; confirm same conversation loads and run_id preserved.
  - Use “New Chat” button to reset run (must create new AgentRun + LLMRun).
- Expected:
  - Transcript shows persisted messages (user, assistant, tool entries).
  - Connection status reflects WebSocket states.
  - New Chat refresh resets transcript and run_id.

#### D. Tool Approvals + Async Execution
- Steps: With Workspace grant containing tool requiring approval, interact via /ws/agents/<slug>/chat/.
- Actions:
  - Trigger tool call (LLM request) and confirm approval card appears.
  - Approve/deny; Celery should run tool async.
  - Observe card updates (`Queued → Running → Completed/Failed`) and transcript includes tool result.
  - Confirm denied tool shows message and Celery task not run.
- Expected:
  - Tool calls persisted as `ToolCall` rows.
  - WebSocket receives `tool_request`, `tool_status`, `tool_result` or `tool_denied`.
  - Tool output appended via `LLMMessage`.

#### E. Transport Status CTA
- Steps: Open agent detail with and without Telegram transport configured.
- Actions:
  - For configured transport, confirm CTA shows “Configured”.
  - For missing transport, CTA prompts to configure and warns.
- Expected:
  - `build_transport_status` rendered text/link.
  - If Telegram credentials are invalid, Celery should log read/timeout issues in poller logs.

### 4. Exit Criteria
- All manual test cases pass with no errors in Django/Celery logs.
- Celery worker shows tool-call lifecycle (queued → running → completed) in logs.
- WebSocket approvals/cards update reliably; transcripts persist tool output.
- No unhandled timeouts reported beyond the retry/logging thresholds.

Record each test run with timestamp, tester name, environment notes, and any issues in this document (append entries below).
