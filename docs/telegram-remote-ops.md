# Telegram Remote Ops

## Purpose

Telegram Remote Ops lets an operator supervise and control agent runs from a paired Telegram chat.
The backend remains canonical for runs, tool calls, approvals, grants, and expirations. Telegram is only a remote control surface.

Current capabilities in this sprint slice:

- shared Telegram bot endpoint per bot token
- local polling fallback without a public tunnel
- optional webhook delivery for a public deployment
- pairing a Telegram chat to an agent
- remote approval tickets with 4-character short codes
- unique prefix resolution for approval fallback, for example `approve 4F`
- inline approval buttons: `Approve`, `Deny`, `Approve Folder`, `Approve Repo`
- inline run control buttons on approval cards: `Status`, `Pause`, `Resume`, `Cancel`
- approval expiration with a default 15-minute TTL
- web UI linkback on every approval card

## Architecture

### Canonical state

Django remains canonical for:

- `AgentRun`
- `ToolCall`
- `ToolApprovalGrant`
- `RemoteApprovalTicket`
- `PendingPairing`
- `CommsConversation`

Telegram never owns approval state. It only triggers actions against canonical backend records.

### Shared bot polling model

Telegram `getUpdates` is scoped to the bot token, not to an individual agent.
Because of that, polling must be owned by one canonical endpoint per bot token.

The implementation now deduplicates polling by bot signature in `backend/comms/tasks.py` using:

- `bot_id`
- then `bot_username`
- then `bot_token_env`
- then endpoint id as a final fallback

This prevents multiple agent pairings that share one Telegram bot from competing over `last_update_id`.

### Remote approval ticket

`RemoteApprovalTicket` lives in `backend/comms/models.py` and binds a pending `ToolCall` to a transport conversation.

Each ticket records:

- `tool_call`
- `run`
- `workspace`
- `transport`
- `endpoint`
- `conversation`
- `external_chat_id`
- `short_code`
- `callback_token`
- `status`
- `expires_at`
- `web_url`
- `scope_options_snapshot`

This model is provider-agnostic by design so WhatsApp or another provider can reuse the same approval lifecycle later.

### Telegram-specific surfaces

- webhook endpoint: `POST /comms/telegram/<endpoint_id>/webhook/`
- pairing text: `pair <PAIR_CODE>` or `/pair <PAIR_CODE>`
- approval fallback text:
  - `approve 4F`
  - `deny 4F`
  - `allow-folder 4F`
  - `allow-repo 4F`

All other free text continues to behave as normal inbound conversation text.

### Cross-talk protections

Remote approval resolution is scoped by:

- paired transport endpoint
- paired external chat ID
- pending ticket status
- ticket expiry window
- callback token for button actions

Short-code prefix resolution only searches active pending approvals inside the same chat conversation.

## Environment

Add or confirm these settings in `backend/.env`:

```env
AGENTMAESTRO_BASE_URL=http://127.0.0.1:8000
REMOTE_APPROVAL_TTL_MINUTES=15
REMOTE_APPROVAL_EXPIRY_INTERVAL_SECONDS=60
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_AGENT_UUID=<agent uuid>
TELEGRAM_CHAT_ID=<optional fixed chat id>
TELEGRAM_ALLOWED_USER_IDS=<optional comma-separated telegram user ids>
TELEGRAM_ENABLE_POLLING=1
TELEGRAM_POLL_INTERVAL_SECONDS=5
TELEGRAM_POLL_TIMEOUT_SECONDS=25
```

Notes:

- `AGENTMAESTRO_BASE_URL` is still used for web UI linkback even in polling mode.
- `TELEGRAM_ENABLE_POLLING=1` is required for Celery beat to enqueue poll jobs.
- local polling does not require a public HTTPS endpoint.

## Backend Setup

From `backend/`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

This applies:

- `comms/migrations/0004_remoteapprovalticket.py`
- `tools/migrations/0007_rename_tools_toola_run_id_97a44f_idx_tools_toola_run_id_ccf71c_idx_and_more.py`

## Local Development: Polling First

This is the recommended local setup when the backend is not exposed publicly.

### Step 1. Configure the bot and clear any webhook

From `backend/`:

```powershell
.\scripts\setTelegramPolling.ps1
```

What it does:

- validates the bot token with Telegram `getMe`
- configures or reuses the shared Telegram bot endpoint in Django
- auto-pairs the agent if `TELEGRAM_CHAT_ID` is set
- skips webhook registration
- calls Telegram `deleteWebhook`
- confirms the bot now has `url: ""` in `getWebhookInfo`

The precondition for polling is that Telegram reports no webhook URL.

### Step 2. Restart local services

Restart:

- Django
- Celery worker
- Celery beat

`TELEGRAM_ENABLE_POLLING=1` is read at Django settings load time, so beat must restart after changing it.

### Step 3. Pair the chat

If `TELEGRAM_CHAT_ID` was not set, the setup flow will leave pairing pending.
Send one of these to the bot from the target Telegram chat:

```text
pair ABCD1234
```

or:

```text
/pair ABCD1234
```

### Step 4. Verify mirrored chat delivery

Send a normal Telegram message to the bot.
It should appear in the mirrored control conversation for the paired agent.

### Step 5. Verify approval flow

Trigger a tool call that requires approval.
Telegram should receive an approval card with:

- the short approval code
- approval buttons
- run control buttons
- an open-run link

## Production Option: Webhook Delivery

Webhook remains supported when the backend is reachable by Telegram on a public allowed HTTPS port.

Telegram only accepts webhook URLs on ports:

- `443`
- `8443`
- `80`
- `88`

If you are not on one of those ports and publicly reachable, use polling instead.

Manual webhook registration shape:

```powershell
$token = $env:TELEGRAM_BOT_TOKEN
$webhookUrl = "https://<public-host>/comms/telegram/<endpoint_id>/webhook/"
$secret = "<webhook secret>"
$body = @{
  url = $webhookUrl
  secret_token = $secret
  allowed_updates = @("message", "callback_query")
  drop_pending_updates = $false
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/setWebhook" -ContentType "application/json" -Body $body
```

## Approval Behavior

When a tool call enters `PENDING_APPROVAL` and the agent has a paired comms conversation:

1. Backend creates a `RemoteApprovalTicket`.
2. A Telegram approval card is sent to the paired chat.
3. The card includes:
   - short approval code
   - tool name
   - summarized args
   - run id
   - agent name
   - expiry time
   - run-control buttons
   - web UI link
4. Operator can approve or deny by button.
5. Operator can also use approval fallback text with a unique short-code prefix.
6. If the TTL expires first, the tool call is denied with reason `Approval expired`.

## Button Layout

Approval card buttons currently include:

- `Approve`
- `Deny`
- `Approve Folder` when path-prefix grant scope is available
- `Approve Repo` when repository grant scope is available
- `Status`
- `Pause`
- `Resume`
- `Cancel`
- `Open Run`

## Unique Prefix Resolution

Tickets are issued with 4-character codes such as `4F7K`.
Operators may type a unique prefix instead of the full code.

Examples:

- `approve 4F`
- `deny 4F7`

Resolution rules:

- minimum prefix length is 2 characters
- lookup is limited to active pending approvals in the same chat
- if more than one match exists, the bot returns an ambiguity message listing the matching codes

## Multi-Provider Direction

The remote-ops approval model is intentionally provider-agnostic.
The generic pieces are:

- `RemoteApprovalTicket`
- `comms.services.remote_ops`
- `send_conversation_message()` in `comms.services.outbound`

Telegram-specific logic currently lives in:

- `comms.transports.telegram`
- `comms.views.telegram_webhook`
- Telegram inline button payloads
- Telegram polling in `comms.tasks.telegram_poll_once`

The WhatsApp integration path should follow the same pattern:

1. add a WhatsApp transport adapter
2. add a WhatsApp webhook or polling endpoint if needed by the provider
3. normalize inbound events into the same internal event shape
4. reuse `RemoteApprovalTicket` and `comms.services.remote_ops`
5. add provider-specific outbound button and message rendering

## Recommended Operator Workflow

For local development:

1. run `backend\scripts\setTelegramPolling.ps1`
2. restart Django, Celery worker, and Celery beat
3. pair with `pair <code>` if needed
4. send a normal Telegram message to confirm mirrored chat delivery
5. trigger a tool approval and verify buttons
6. use the web UI link on the approval card when full context is needed

## Next Recommended Build Stages

1. Add richer standalone status cards so run controls are available even when no approval is pending.
2. Edit Telegram approval messages after action so stale buttons are visually retired.
3. Add a connection status view that shows active poll owner, last update id, and last poll time.
4. Add WhatsApp transport scaffolding reusing the same ticket model and callback action service.
5. Add a focused end-to-end runbook for Telegram Remote Ops.
