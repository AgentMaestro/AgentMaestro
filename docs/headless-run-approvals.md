# Headless Run Approvals

Scheduled headless runs use a narrow approval-reuse model.

## First Run

The first headless execution for a scheduled task does not go straight into tool execution.

Instead:

1. the scheduler creates a real `AgentRun`
2. the run enters `WAITING_FOR_APPROVAL`
3. an internal approval-gate `ToolCall` is created with tool name `scheduled_headless_run_gate`
4. the existing approval surfaces can approve or deny that gate
5. when approved, the gate records a reusable `ScheduledTaskApproval` and then queues the headless run for execution

If the gate is denied or expires, the run is failed and the scheduled task is cleared for a future occurrence.

## Approval Fingerprint

Approval reuse is based on a deterministic fingerprint built from:

- `scheduled_task_id`
- `agent_id`
- `execution_mode`
- `task_type`
- `delivery_target`
- `timezone`
- `local_time`
- normalized `execution_payload`
- normalized allowed-tool signature for the agent at launch time

The tool signature currently includes, per allowed tool:

- `tool_name`
- effective `risk`
- effective `requires_approval`

This keeps approval reuse narrow. A later run only inherits if it still looks like the same scheduled job.

## Reuse Rules

A future headless execution inherits approval only when:

- the scheduled task matches the same fingerprint
- the approval has not expired
- the approval has not been revoked

When approval is inherited:

- the run records `approval_mode="inherited"`
- the run stores the fingerprint and approval reference
- the `ScheduledTaskApproval` usage counters are updated
- the run proceeds without a fresh manual approval gate

## Drift And Expiry

A fresh approval is required when the current execution no longer matches an active approval.

Current drift triggers include:

- task payload changed
- task type changed
- delivery target changed
- allowed tools changed
- effective tool risk changed
- agent changed
- previous approval expired

If the task drifts after the approval request was created but before approval is executed, the gate refuses to continue the run and marks the run failed with `fingerprint_drift`.

## Persistence And Audit

The durable approval record is `ScheduledTaskApproval`.

It stores:

- scheduled task
- source run
- source approval gate tool call
- fingerprint and version
- normalized execution payload snapshot
- tool signature snapshot
- approver
- approval timestamp
- expiration timestamp
- use count
- last used timestamp

Each `AgentRun` also stores:

- `approval_mode`
- `approval_fingerprint`
- `approval_source_ref`

Run events record:

- approval requested
- approval granted
- approval inherited
- approval denied
- approval drifted

## Configuration

- `SCHEDULED_HEADLESS_APPROVAL_TTL_DAYS=30`

## Current Limits

Still deferred:

- broad approval templates across multiple scheduled tasks
- approval inheritance across different agents or tasks
- UI for managing/revoking scheduled-task approvals directly
- per-task approval caching beyond exact fingerprint reuse
