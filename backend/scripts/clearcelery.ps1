Set-Location "C:\Dev\AgentMaestro\backend"
python -m celery -A agentmaestro purge -f
Write-Host "Celery task purge complete."
