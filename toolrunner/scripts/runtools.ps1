# start Fast API
Write-Host "Starting ToolRunner FastAPI app"
Set-Location "C:\Dev\AgentMaestro"
./toolrunner/.venv/Scripts/uvicorn toolrunner.app.main:app --host 0.0.0.0 --port 8001 --reload


