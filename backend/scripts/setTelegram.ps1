param(
    [string]$AgentUuid = $env:TELEGRAM_AGENT_UUID,
    [string]$AgentSlug = $env:TELEGRAM_AGENT_SLUG,
    [string]$BotToken = $env:TELEGRAM_BOT_TOKEN,
    [string]$BotTokenEnv = $(if ($env:TELEGRAM_BOT_TOKEN_ENV) { $env:TELEGRAM_BOT_TOKEN_ENV } else { "TELEGRAM_BOT_TOKEN" }),
    [string]$BaseUrl = $env:AGENTMAESTRO_BASE_URL,
    [string]$ChatId = $env:TELEGRAM_CHAT_ID,
    [string]$AllowedUserIds = $env:TELEGRAM_ALLOWED_USER_IDS,
    [switch]$SkipWebhook,
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
$pythonExe = Join-Path $backendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Backend virtualenv python not found at $pythonExe"
}

Load-DotEnv -Path (Join-Path $backendRoot ".env")

if (-not $AgentUuid) { $AgentUuid = $env:TELEGRAM_AGENT_UUID }
if (-not $AgentSlug) { $AgentSlug = $env:TELEGRAM_AGENT_SLUG }
if (-not $BotToken) { $BotToken = $env:TELEGRAM_BOT_TOKEN }
if (-not $BaseUrl) { $BaseUrl = $env:AGENTMAESTRO_BASE_URL }
if (-not $ChatId) { $ChatId = $env:TELEGRAM_CHAT_ID }
if (-not $AllowedUserIds) { $AllowedUserIds = $env:TELEGRAM_ALLOWED_USER_IDS }

if (-not $AgentUuid -and -not $AgentSlug) {
    throw "Set TELEGRAM_AGENT_UUID or TELEGRAM_AGENT_SLUG before running this script."
}

if (-not $BotToken) {
    throw "Set TELEGRAM_BOT_TOKEN before running this script."
}

Write-Host "Validating Telegram bot token..."
$botInfo = Invoke-RestMethod -Method Get -Uri "https://api.telegram.org/bot$BotToken/getMe"
if (-not $botInfo.ok) {
    throw "Telegram getMe did not return ok=true."
}
$botResult = $botInfo.result
$botDisplayName = ((@($botResult.first_name, $botResult.last_name) | Where-Object { $_ }) -join " ")
if (-not $botDisplayName) {
    $botDisplayName = $botResult.username
}

$env:AM_TELEGRAM_AGENT_UUID = "$AgentUuid"
$env:AM_TELEGRAM_AGENT_SLUG = "$AgentSlug"
$env:AM_TELEGRAM_CHAT_ID = "$ChatId"
$env:AM_TELEGRAM_ALLOWED_USER_IDS = "$AllowedUserIds"
$env:AM_TELEGRAM_BOT_TOKEN_ENV = "$BotTokenEnv"
$env:AM_TELEGRAM_BOT_ID = "$($botResult.id)"
$env:AM_TELEGRAM_BOT_USERNAME = "$($botResult.username)"
$env:AM_TELEGRAM_BOT_NAME = "$botDisplayName"
$env:DJANGO_SETTINGS_MODULE = "agentmaestro.settings.dev"

$pythonScript = @"
import json
import os
import uuid

import django
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "agentmaestro.settings.dev")
django.setup()

from agents.models import Agent
from comms.models import CommsConversation, PendingPairing, Transport, TransportEndpoint, generate_callback_token
from control.models import ControlConversation

agent_uuid = (os.getenv("AM_TELEGRAM_AGENT_UUID") or "").strip()
agent_slug = (os.getenv("AM_TELEGRAM_AGENT_SLUG") or "").strip()
chat_id = (os.getenv("AM_TELEGRAM_CHAT_ID") or "").strip()
allowed_user_ids_raw = (os.getenv("AM_TELEGRAM_ALLOWED_USER_IDS") or "").strip()
bot_token_env = (os.getenv("AM_TELEGRAM_BOT_TOKEN_ENV") or "TELEGRAM_BOT_TOKEN").strip()
bot_id = (os.getenv("AM_TELEGRAM_BOT_ID") or "").strip()
bot_username = (os.getenv("AM_TELEGRAM_BOT_USERNAME") or "").strip()
bot_name = (os.getenv("AM_TELEGRAM_BOT_NAME") or "").strip()

allowed_user_ids = [item.strip() for item in allowed_user_ids_raw.split(",") if item.strip()]

agent = None
if agent_uuid:
    try:
        agent = Agent.objects.select_related("workspace").filter(id=uuid.UUID(agent_uuid)).first()
    except ValueError:
        agent = None
if agent is None and agent_slug:
    agent = Agent.objects.select_related("workspace").filter(slug=agent_slug).first()
if agent is None:
    raise SystemExit("Unable to find agent from TELEGRAM_AGENT_UUID / TELEGRAM_AGENT_SLUG")

transport, _ = Transport.objects.get_or_create(
    key="telegram",
    defaults={"display_name": "Telegram", "mode": "both", "is_enabled": True},
)
changed_transport = []
if transport.display_name != "Telegram":
    transport.display_name = "Telegram"
    changed_transport.append("display_name")
if transport.mode != "both":
    transport.mode = "both"
    changed_transport.append("mode")
if not transport.is_enabled:
    transport.is_enabled = True
    changed_transport.append("is_enabled")
if changed_transport:
    transport.save(update_fields=changed_transport)

endpoint = None
for candidate in TransportEndpoint.objects.filter(transport=transport, kind="bot").order_by("-id"):
    candidate_config = candidate.config or {}
    if bot_id and str(candidate_config.get("bot_id") or "") == bot_id:
        endpoint = candidate
        break
if endpoint is None:
    for candidate in TransportEndpoint.objects.filter(transport=transport, kind="bot").order_by("-id"):
        candidate_config = candidate.config or {}
        if bot_username and str(candidate_config.get("bot_username") or "") == bot_username:
            endpoint = candidate
            break
if endpoint is None:
    for candidate in TransportEndpoint.objects.filter(transport=transport, kind="bot").order_by("-id"):
        candidate_config = candidate.config or {}
        if bot_token_env and str(candidate_config.get("bot_token_env") or "") == bot_token_env:
            endpoint = candidate
            break
if endpoint is None:
    endpoint = TransportEndpoint(transport=transport, kind="bot", config={})

config = dict(endpoint.config or {})
merged_allowed_user_ids = sorted({*map(str, config.get("allow_user_ids") or []), *allowed_user_ids})
config.update(
    {
        "bot_token_env": bot_token_env,
        "allow_user_ids": merged_allowed_user_ids,
        "bot_id": bot_id,
        "bot_username": bot_username,
        "bot_name": bot_name,
        "webhook_secret": str(config.get("webhook_secret") or generate_callback_token(20)),
    }
)
config.pop("agent_id", None)
endpoint.kind = "bot"
endpoint.config = config
endpoint.save()

pair_code = ""
paired = False
control_uuid = ""
conversation_id = None

if chat_id:
    control_conversation = getattr(agent, "default_conversation", None)
    if control_conversation is None:
        control_conversation = ControlConversation.objects.create(
            kind="comms_mirror",
            title=f"{agent.name} Telegram",
        )
    conversation, _ = CommsConversation.objects.get_or_create(
        endpoint=endpoint,
        external_conversation_id=chat_id,
        defaults={
            "transport": transport,
            "title": f"{agent.name} Telegram",
            "control_conversation": control_conversation,
        },
    )
    updated_conversation = []
    if conversation.transport_id != transport.id:
        conversation.transport = transport
        updated_conversation.append("transport")
    if not conversation.control_conversation_id:
        conversation.control_conversation = control_conversation
        updated_conversation.append("control_conversation")
    if updated_conversation:
        conversation.save(update_fields=updated_conversation)
    if agent.default_conversation_id != conversation.control_conversation_id:
        agent.default_conversation = conversation.control_conversation
        agent.save(update_fields=["default_conversation"])
    pairing = PendingPairing.objects.filter(agent=agent, endpoint=endpoint).order_by("-created_at").first()
    if pairing is None:
        pairing = PendingPairing.objects.create(agent=agent, endpoint=endpoint)
    if pairing.status != PendingPairing.STATUS_CLAIMED or pairing.claimed_chat_id != chat_id:
        pairing.claimed_chat_id = chat_id
        pairing.claimed_at = timezone.now()
        pairing.status = PendingPairing.STATUS_CLAIMED
        pairing.save(update_fields=["claimed_chat_id", "claimed_at", "status"])
    pair_code = pairing.pair_code
    paired = True
    control_uuid = str(conversation.control_conversation.uuid)
    conversation_id = conversation.id
else:
    now = timezone.now()
    pairing = PendingPairing.objects.filter(agent=agent, endpoint=endpoint).order_by("-created_at").first()
    if pairing and pairing.status == PendingPairing.STATUS_PENDING and pairing.expires_at > now:
        pass
    else:
        if pairing and pairing.status == PendingPairing.STATUS_PENDING and pairing.expires_at <= now:
            pairing.status = PendingPairing.STATUS_EXPIRED
            pairing.save(update_fields=["status"])
        pairing = PendingPairing.objects.create(agent=agent, endpoint=endpoint)
    pair_code = pairing.pair_code

payload = {
    "agent_id": str(agent.id),
    "agent_name": agent.name,
    "agent_slug": agent.slug,
    "endpoint_id": endpoint.id,
    "webhook_secret": str(endpoint.config.get("webhook_secret") or ""),
    "pair_code": pair_code,
    "paired": paired,
    "chat_id": chat_id,
    "control_conversation_uuid": control_uuid,
    "conversation_id": conversation_id,
}
print(json.dumps(payload))
"@

Set-Location $backendRoot
$stateJson = $pythonScript | & $pythonExe -
if (-not $stateJson) {
    throw "Failed to configure Telegram endpoint in Django."
}
$state = $stateJson | ConvertFrom-Json

$webhookUrl = $null
if (-not $SkipWebhook) {
    if (-not $BaseUrl) {
        throw "Set AGENTMAESTRO_BASE_URL or pass -SkipWebhook."
    }
    $webhookUrl = ($BaseUrl.TrimEnd('/')) + "/comms/telegram/$($state.endpoint_id)/webhook/"
    if ($webhookUrl -match "127\.0\.0\.1|localhost") {
        Write-Warning "Webhook URL points at localhost. Telegram cannot reach a local-only URL unless you expose it publicly through a tunnel or reverse proxy."
    }
    Write-Host "Registering Telegram webhook at $webhookUrl ..."
    $webhookBody = @{
        url = $webhookUrl
        secret_token = $state.webhook_secret
        allowed_updates = @("message", "callback_query")
        drop_pending_updates = $false
    } | ConvertTo-Json -Depth 5
    $webhookResponse = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$BotToken/setWebhook" -ContentType "application/json" -Body $webhookBody
    if (-not $webhookResponse.ok) {
        throw "Telegram setWebhook did not return ok=true."
    }
}

if ($ChatId -and -not $SkipPairMessage) {
    try {
        $pairText = if ($state.paired) {
            "Telegram Remote Ops connected to $($state.agent_name). This chat is paired and ready for approvals and run controls."
        } else {
            "Telegram Remote Ops prepared for $($state.agent_name). Send pair $($state.pair_code) to finish pairing."
        }
        $sendMessageBody = @{ chat_id = $ChatId; text = $pairText } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$BotToken/sendMessage" -ContentType "application/json" -Body $sendMessageBody | Out-Null
    }
    catch {
        Write-Warning "Unable to send Telegram confirmation message to chat $ChatId. The pairing/configuration state was still updated in Django. $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "Telegram configuration complete." -ForegroundColor Green
Write-Host "Agent: $($state.agent_name) [$($state.agent_id)]"
Write-Host "Bot: @$($botResult.username) ($botDisplayName)"
Write-Host "Endpoint ID: $($state.endpoint_id)"
if ($webhookUrl) {
    Write-Host "Webhook URL: $webhookUrl"
    Write-Host "Webhook Secret: $($state.webhook_secret)"
}
if ($state.paired) {
    Write-Host "Pairing: claimed chat $($state.chat_id)"
    if ($state.control_conversation_uuid) {
        Write-Host "Control Conversation UUID: $($state.control_conversation_uuid)"
    }
} else {
    Write-Host "Pairing: pending"
    Write-Host "Pair Code: $($state.pair_code)"
    Write-Host "Next Step: send pair $($state.pair_code) to @$($botResult.username)"
}
if ($AllowedUserIds) {
    Write-Host "Allowed Telegram user IDs: $AllowedUserIds"
}

[ordered]@{
    ok = $true
    agent_id = $state.agent_id
    agent_name = $state.agent_name
    endpoint_id = $state.endpoint_id
    webhook_url = $webhookUrl
    webhook_secret = $state.webhook_secret
    paired = [bool]$state.paired
    pair_code = $state.pair_code
    chat_id = $state.chat_id
    bot_username = $botResult.username
    control_conversation_uuid = $state.control_conversation_uuid
} | ConvertTo-Json -Depth 5
