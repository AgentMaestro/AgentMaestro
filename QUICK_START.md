## Quick Start

Get a local dev workspace running in about five minutes:

1. **Clone & prepare**
   ```bash
   git clone https://github.com/<your-org>/AgentMaestro.git
   cd AgentMaestro
   python -m venv .venv
   .venv/Scripts/activate  # use `source .venv/bin/activate` on mac/linux
   pip install -r backend/requirements.txt
   pip install -r toolrunner/requirements.txt
   ```

2. **Postgres & Redis (recommended)**
   - Start Postgres and create a database (`agentmaestro` by default).
   - Start Redis on `127.0.0.1:6379`.
   - Update `backend/agentmaestro/settings/local.py` if you use non-default credentials.

3. **Migrate & load fixtures**
   ```bash
   cd backend
   python manage.py migrate
   python manage.py loaddata initial_data.json  # optional starter data
   ```

4. **Run services**
   - **Django + ASGI (Daphne)**: `daphne -b 127.0.0.1 -p 8000 agentmaestro.asgi:application`
     (alternatively `uvicorn agentmaestro.asgi:application --host 127.0.0.1 --port 8000 --reload`)
   - **Celery worker**: `python -m celery -A agentmaestro worker --loglevel=info --pool=solo`
   - **Toolrunner**: `cd ../toolrunner && .venv/Scripts/uvicorn app.main:app --reload`

5. **Start a run**
   - Visit `http://127.0.0.1:8000/ui/dev/start-run/` in your browser.
   - Click **Start Run**; the websocket updates show tick progress.
   - When you need tool capabilities, the backend calls toolrunner via the documented `/v1/execute` contract.

6. **Testing**
   - Powershell test scripts are written for each app and are located in each apps' /scripts folder
   - Backend: `cd backend && scripts/test.ps1`
   - Toolrunner unit tests: `cd toolrunner && scripts/test.ps1`

7. **Docs & updates**
   - Architecture overview: `ARCHITECTURE.md`
   - Toolrunner patch workflow: `docs/toolrunner.md`
   - Performance baseline: `docs/perf.md`

Keep an eye on the `C:\Dev\AgentMaestro\temp` basetemp when running pytest on Windows; adjust `PYTEST_ADDOPTS` to a writable folder before launching `scripts/test.ps1`.
