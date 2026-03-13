# start Fast API

# Set ENV variables
$env:TOOLRUNNER_SECRET="573f591d66edbbd4db7bb241cab40dd542279e7716206dd883931c82f5859acb"
$env:TOOLRUNNER_SANDBOX_ROOT="/tmp/agentmaestro/sandbox"
$env:TOOLRUNNER_TIMEOUT="15"
$env:TOOLRUNNER_OUTPUT_LIMIT="4096"
$env:TOOLRUNNER_ALLOWED_COMMANDS="python"
$env:TOOLRUNNER_PYTHON=".venv\Scripts\python.exe"
$env:TOOLRUNNER_BASE_URL="http://127.0.0.1:8001"
$env:TOOLRUNNER_ALLOW_TO_SEARCH_LIST="C:/Dev/AgentMaestro,C:/tmp/agentmaestro/sandbox"
$env:TOOLRUNNER_EXCLUDE_FROM_SEARCH_LIST="**/.git/**,**/.venv/**,**/node_modules/**,**/__pycache__/**,**/.pytest_cache/**,**/media/**,**/staticfiles/**,**/*.pyc,**/*.sqlite3,.env"

Write-Host "Starting ToolRunner FastAPI app"
Set-Location "C:\Dev\AgentMaestro"
./toolrunner/.venv/Scripts/uvicorn toolrunner.app.main:app --host 127.0.0.1 --port 8001 --reload


