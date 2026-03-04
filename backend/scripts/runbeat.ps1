# start celery
Write-Host "Starting Celery Beat"
Set-Location "C:\Dev\AgentMaestro\backend"
python -m celery -A agentmaestro beat -l info