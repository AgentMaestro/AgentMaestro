Write-Host "Starting Stage 6 helper command list..."
$commands = @(
    "cd backend",
    ".\.venv\Scripts\activate",
    ".venv\Scripts\python.exe manage.py migrate",
    ".venv\Scripts\python.exe manage.py seed_tools",
    "redis-server --port 6379",
    ".venv\Scripts\python.exe manage.py runserver",
    ".venv\Scripts\python.exe -m celery -A agentmaestro worker --loglevel=info --pool=solo"
)
foreach ($cmd in $commands) {
    Write-Host "`n`e[36m$cmd`e[0m"
}
