param(
    [string]$AgentUuid = $env:TELEGRAM_AGENT_UUID,
    [string]$AgentSlug = $env:TELEGRAM_AGENT_SLUG,
    [string]$BotToken = $env:TELEGRAM_BOT_TOKEN,
    [string]$BotTokenEnv = $(if ($env:TELEGRAM_BOT_TOKEN_ENV) { $env:TELEGRAM_BOT_TOKEN_ENV } else { "TELEGRAM_BOT_TOKEN" }),
    [string]$ChatId = $env:TELEGRAM_CHAT_ID,
    [string]$AllowedUserIds = $env:TELEGRAM_ALLOWED_USER_IDS,
    [switch]$SkipPairMessage
)

$ErrorActionPreference = "Stop"

function Load-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Split("#", 2)[0].Trim()
        if (-not [string]::IsNullOrWhiteSpace($key) -and -not (Test-Path "Env:$key")) {
            Set-Item -Path "Env:$key" -Value $value
        }
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$backendRoot = Split-Path -Parent $scriptRoot
Load-DotEnv -Path (Join-Path $backendRoot ".env")

if (-not $AgentUuid) { $AgentUuid = $env:TELEGRAM_AGENT_UUID }
if (-not $AgentSlug) { $AgentSlug = $env:TELEGRAM_AGENT_SLUG }
if (-not $BotToken) { $BotToken = $env:TELEGRAM_BOT_TOKEN }
if (-not $ChatId) { $ChatId = $env:TELEGRAM_CHAT_ID }
if (-not $AllowedUserIds) { $AllowedUserIds = $env:TELEGRAM_ALLOWED_USER_IDS }

if (-not $BotToken) {
    throw "Set TELEGRAM_BOT_TOKEN before running this script."
}

$setTelegramScript = Join-Path $scriptRoot "setTelegram.ps1"
if (-not (Test-Path $setTelegramScript)) {
    throw "setTelegram.ps1 not found at $setTelegramScript"
}

$setupArgs = @{
    SkipWebhook = $true
}
if ($AgentUuid) { $setupArgs.AgentUuid = $AgentUuid }
if ($AgentSlug) { $setupArgs.AgentSlug = $AgentSlug }
if ($BotToken) { $setupArgs.BotToken = $BotToken }
if ($BotTokenEnv) { $setupArgs.BotTokenEnv = $BotTokenEnv }
if ($ChatId) { $setupArgs.ChatId = $ChatId }
if ($AllowedUserIds) { $setupArgs.AllowedUserIds = $AllowedUserIds }
if ($SkipPairMessage) { $setupArgs.SkipPairMessage = $true }

Write-Host "Configuring Telegram bot endpoint in Django (polling mode)..."
& $setTelegramScript @setupArgs

Write-Host "Removing Telegram webhook so polling can own the update stream..."
$deleteBody = @{ drop_pending_updates = $false } | ConvertTo-Json
$deleteResponse = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$BotToken/deleteWebhook" -ContentType "application/json" -Body $deleteBody
if (-not $deleteResponse.ok) {
    throw "Telegram deleteWebhook did not return ok=true."
}

$webhookInfo = Invoke-RestMethod -Method Get -Uri "https://api.telegram.org/bot$BotToken/getWebhookInfo"
if (-not $webhookInfo.ok) {
    throw "Telegram getWebhookInfo did not return ok=true."
}

Write-Host ""
Write-Host "Telegram polling setup complete." -ForegroundColor Green
Write-Host "Webhook URL: $($webhookInfo.result.url)"
Write-Host "Pending update count: $($webhookInfo.result.pending_update_count)"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Set TELEGRAM_ENABLE_POLLING=1 in backend/.env"
Write-Host "2. Restart Django, Celery worker, and Celery beat"
Write-Host "3. Send 'pair <code>' or '/pair <code>' to the bot if pairing is still pending"
Write-Host "4. Send a normal chat message to the bot and confirm it appears in the mirrored control chat"
