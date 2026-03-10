param(
    [int]$Port = 8000,
    [string]$Debug = $(if ($env:OPENAI_WS_DEBUG) { $env:OPENAI_WS_DEBUG } else { "0" }),
    [string]$Transport = $(if ($env:OPENAI_TRANSPORT) { $env:OPENAI_TRANSPORT } else { "ws" }),
    [string]$Mode = $(if ($env:OPENAI_HTTP_MODE) { $env:OPENAI_HTTP_MODE } else { "responses" })
)

# set env variables before launching Daphne so the defaults above stay in sync with what gets exported
$env:DJANGO_SETTINGS_MODULE = "agentmaestro.settings.dev"
$env:OPENAI_WS_DEBUG = "0"
$env:PYTHONUNBUFFERED = "1"
$env:OPENAI_TRANSPORT = $Transport
$env:OPENAI_HTTP_MODE = $Mode

Write-Host "Starting Daphne using $Transport at 127.0.0.1:$Port with OPENAI_WS_DEBUG=$Debug, OPENAI_TRANSPORT=$Transport, OPENAI_HTTP_MODE=$Mode"
Set-Location "C:\Dev\AgentMaestro\backend"
python -m daphne -b 127.0.0.1 -p $Port agentmaestro.asgi:application
