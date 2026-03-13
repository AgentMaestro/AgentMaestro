 $token = $env:TELEGRAM_BOT_TOKEN
  $webhookUrl = "https://127.0.0.1:8000/comms/telegram/3/webhook/"
  $secret = "j1ad39aw1brsy7rl6vsu"

  $body = @{
    url = $webhookUrl
    secret_token = $secret
    allowed_updates = @("message", "callback_query")
    drop_pending_updates = $false
  } | ConvertTo-Json -Depth 5

  Invoke-RestMethod `
    -Method Post `
    -Uri "https://api.telegram.org/bot$token/setWebhook" `
    -ContentType "application/json" `
    -Body $body