## Local Runbook / Commands (Windows PowerShell)

### 1. Setup Environment
1. Activate venv:
   ```powershell
   cd backend
   .\.venv\Scripts\activate
   ```
2. Ensure env vars configured:
   ```powershell
   cp .env.example .env
   # edit .env for DATABASE_URL, REDIS/TELEGRAM keys, CELERY_BROKER_URL, etc.
   ```

### 2. Start Dependencies
1. Redis (required for Celery + Channels):
   ```powershell
   redis-server --port 6379
   ```
   - Keep console open; look for `Ready to accept connections`.
2. PostgreSQL (ensure service running—no extra command needed if installed).

### 3. Run Migrations + Seed Data
```powershell
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py seed_tools
```
_Use `--include-unreleased` when you also want Tier 2 / unreleased tools seeded into a workspace._

### 4. Start Servers
1. Django development server:
   ```powershell
   .venv\Scripts\python.exe manage.py runserver
   ```
2. Celery worker (solo pool on Windows):
   ```powershell
   .venv\Scripts\python.exe -m celery -A agentmaestro worker --loglevel=info --pool=solo
   ```
3. (Optional) Celery Beat for scheduled tasks:
   ```powershell
   .venv\Scripts\python.exe -m celery -A agentmaestro beat --loglevel=info
   ```

### 5. Log Locations
- **Django console**: runserver output shows HTTP requests, tool approval errors, websocket connect/disconnects.
- **Celery console**: watch for `Task tools.execute_tool_call_async` start/completion, Telegram poll retries, and `tool_call_status` logs.
- **Browser DevTools**:
  - Console/network: ensure WS messages (type `tool_request`, `tool_status`, `tool_result`) appear.
  - Network tab: inspect `/ws/agents/<slug>/chat/` frames or errors.

### 6. Common Failure Symptoms
- **WebSocket never connects**: verify runserver reachable, channel layer configured, and WS URL `/ws/agents/<slug>/chat/` matches host. Check Redis/Channels.
- **Tool results never show**: ensure Celery worker is running, tasks queue visible in worker log, and `tools.execute_tool_call_async` task completes without exceptions.
- **Too many Telegram timeouts**: watch Celery log; check `TELEGRAM_POLL_TIMEOUT_RETRIES` (default 3). Confirm network to Telegram is stable.
- **NN worker not receiving tasks**: confirm Celery worker prints `Task ... received`; if not, verify `CELERY_BROKER_URL` matches Redis, and `redis-server` is running.

### 7. Shutdown Sequence (reverse order)
1. Stop Celery worker/beat (Ctrl+C).
2. Stop Django runserver (Ctrl+C).
3. Stop Redis if launched manually.
