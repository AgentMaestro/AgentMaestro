# start celery
Write-Host "Starting Celery"
Set-Location "C:\Dev\AgentMaestro\backend"
python -m celery -A agentmaestro worker --loglevel=info --pool=solo
