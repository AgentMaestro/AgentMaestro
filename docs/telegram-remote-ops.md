# Telegram Remote Ops

## Purpose

Telegram Remote Ops lets an operator use Telegram as a remote transport for the canonical `agents/<slug>` chat and its approval flow.
Django remains canonical for runs, tool calls, approvals, grants, expirations, and chat events.
Telegram does not own state. It forwards operator input into the normal agent run and receives the same final assistant replies and approval requests.

Current capabilities in this completed sprint slice:

- shared Telegram bot endpoint per bot token
- local polling fallback without a public tunnel
- optional webhook delivery for a public deployment
- pairing a Telegram chat to an agent
- Telegram input bridged into the canonical `agents/<slug>` chat
- browser user prompts mirrored out to Telegram for a complete shared transcript
- final assistant replies mirrored to both browser and Telegram
- remote approval tickets with 4-character short codes
- unique prefix resolution for approval fallback, for example `approve 4F`
- inline approval buttons: `Approve`, `Deny`, `Approve Folder`, `Approve Repo`
- inline run control buttons on approval cards: `Status`, `Cancel`
- terminal approval-card updates after action or expiry
- approval expiration with a default 15-minute TTL

Deliberately not in this slice:

- Telegram `Pause` and `Resume` buttons
- `Open Run` button on approval cards
- starting a brand-new run from Telegram when no browser-backed run session exists

## Architecture

### Canonical state

Django remains canonical for:

- `AgentRun`
- `ToolCall`
- `ToolApprovalGrant`
- `RemoteApprovalTicket`
- `PendingPairing`
- `CommsConversation`
- run events broadcast into `agents/<slug>`

Telegram never owns approval or orchestration state. It only triggers actions against canonical backend records.

### Canonical chat model

Telegram is not a separate operator chat and not a separate approval system.
It is a transport bridge for the normal agent chat.

Current behavior:

- browser user message -> appears in browser chat and is mirrored to Telegram
- Telegram user message -> enters the same agent run and appears in browser chat
- final assistant reply -> appears in browser chat and Telegram
- approval actions from Telegram -> emit system-style messages into the canonical agent chat

### Shared bot polling model

Telegram `getUpdates` is scoped to the bot token, not to an individual agent.
Because of that, polling is owned by one canonical endpoint per bot token.

The implementation deduplicates polling by bot signature in `backend/comms/tasks.py` using:

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
- `external_message_id`
- `short_code`
- `callback_token`
- `status`
- `expires_at`
- `acted_at`
- `acted_by_label`
- `scope_options_snapshot`

This model is provider-agnostic by design so WhatsApp or another provider can reuse the same approval lifecycle later.

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

- `AGENTMAESTRO_BASE_URL` is still used for URL generation even in polling mode.
- `TELEGRAM_ENABLE_POLLING=1` is required for Celery beat to enqueue poll jobs.
- local polling does not require a public HTTPS endpoint.

## Backend Setup

From `backend/`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

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

- Django or Daphne
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

### Step 4. Verify canonical chat delivery

1. Open `agents/<slug>` in the browser.
2. Make sure the websocket-backed run session is active.
3. Send a browser message and confirm it appears in Telegram.
4. Send a Telegram message and confirm it appears in the browser chat.
5. Confirm final assistant replies appear in both places.

Current limitation:

- Telegram input attaches to an active browser-backed run session.
- Telegram does not yet create a new run by itself when no live agent chat session exists.

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

When a tool call enters `PENDING_APPROVAL` and the agent has a paired Telegram conversation:

1. Backend creates a `RemoteApprovalTicket`.
2. A Telegram approval card is sent to the paired chat.
3. The card includes:
   - short approval code
   - tool name
   - summarized args
   - run id
   - agent name
   - expiry time
   - approval buttons
   - limited run-control buttons
4. Operator can approve or deny by button.
5. Operator can also use typed fallback with a unique short-code prefix.
6. If the TTL expires first, the tool call is denied with reason `Approval expired`.
7. After action or expiry, the original Telegram approval card is edited to terminal state and stale buttons are removed.
8. The action also emits into the canonical `agents/<slug>` chat as a system-style remote-ops message.

### Button layout

Pending approval card buttons currently include:

- `Approve`
- `Deny`
- `Approve Folder` when path-prefix grant scope is available
- `Approve Repo` when repository grant scope is available
- `Status`
- `Cancel`

Terminal approval cards keep no action buttons.

### Typed fallback

Typed fallback commands currently supported:

- `approve 4F`
- `deny 4F`
- `allow-folder 4F`
- `allow-repo 4F`

Resolution rules:

- minimum prefix length is 2 characters
- lookup is limited to active pending approvals in the same chat
- if more than one match exists, the bot returns an ambiguity message listing the matching codes

### Browser chat and tool-card effects

Approval actions taken from Telegram now affect the normal browser agent chat:

- a remote-ops system message is appended into `agents/<slug>`
- the related tool card is updated normally through the canonical run events
- approval detail such as `Approved via Telegram.` or denial detail such as `Denied via Telegram.` can appear in the tool card response area

## Multi-Provider Direction

The remote-ops approval model is intentionally provider-agnostic.
The generic pieces are:

- `RemoteApprovalTicket`
- `comms.services.remote_ops`
- `send_conversation_message()` and `edit_conversation_message()` in `comms.services.outbound`

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
4. open `agents/<slug>` and start the chat session
5. send a browser message and confirm it appears in Telegram
6. send a Telegram message and confirm it appears in browser chat
7. trigger a tool approval and verify button and typed-command approval flows
8. confirm the terminal approval card is edited in Telegram after action

## Sprint Close-Out Status

Completed in this sprint:

- canonical browser/Telegram chat bridge for the agent chat
- shared transcript mirroring between browser and Telegram
- Telegram approval buttons and typed fallback commands
- 4-character approval codes with unique-prefix resolution
- approval-card terminal updates after action and expiry
- approval action messages routed into canonical `agents/<slug>` chat
- approval smoke test for button and typed-command flows

Deferred intentionally:

- Telegram `Pause` and `Resume`
- standalone Telegram status/control cards outside approval cards
- start-run-from-Telegram when no active browser chat session exists
- WhatsApp transport implementation

## Next Recommended Build Stages

1. Start runs directly from Telegram when no active browser-backed session exists.
2. Add standalone Telegram status cards so run controls are available even when no approval is pending.
3. Add WhatsApp transport scaffolding reusing the same ticket model and callback action service.
4. Add a focused end-to-end runbook for Telegram Remote Ops.
