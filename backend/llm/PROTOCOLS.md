# LLM Transport Protocols

This document describes the message and tool protocol used by the LLM layer in words, with the exact transport differences called out per provider.

## Shared Model

- The agent system prompt is built by `llm.system_context.build_system_context`.
- That prompt always starts with the base kernel and then layers in role guidance, bootstrap status, runtime facts, sandbox notes, capability notices, model notice, authenticated user info, agent description, and any agent-specific instruction text.
- Tool schemas are not embedded in the system prompt. They are sent separately as provider tool metadata.
- `SHOW_CONDENSED_SYSTEM_LOGS=1` means the UI/system stream shows summaries. `SHOW_CONDENSED_SYSTEM_LOGS=0` means the UI/system stream shows the exact JSON payloads and raw provider responses.

## Consumer Contract

`AgentChatConsumer` should remain provider-neutral. It should coordinate run state, local history, bootstrap state, and tool lifecycle, but it should not hard-code provider-specific behavior into orchestration logic.

- Keep provider branching at the adapter boundary, not in the consumer.
- Treat run state, tool calls, and bootstrap completion as local canonical state.
- Convert that canonical state into provider wire formats only when building the outbound request.
- Preserve run history in a provider-neutral shape first, then let the transport layer serialize it for OpenAI WS, OpenAI HTTP, OpenAI Chat Completions, or Gemini HTTP.
- Use normalized identifiers and state transitions so reconnects, retries, and provider failover all follow the same run lifecycle.

## Normalization Rules

- Provider selection is normalized from the selected model when the profile and model disagree.
- `previous_response_id` is the OpenAI continuity token, not a general tool identifier.
- `provider_call_id` is the provider-specific tool call identifier and must be echoed back only when the provider protocol requires it.
- Tool outputs stay in local history as canonical events first, then become `function_call_output` or `functionResponse` payloads as needed.
- Gemini HTTP rebuilds from local history and provider-native contents; OpenAI WS and OpenAI HTTP preserve continuity with `previous_response_id` where available.

## OpenAI WS

- Initial request shape:
  - The transport sends a `response.create` request.
  - The request contains a system message and a user message.
  - The request also carries tool definitions on the first send.
- What the model sees:
  - The model gets the full system prompt.
  - The model gets the current user text.
  - Tool availability is provided separately through the `tools` payload.
- Tool calls:
  - The model returns tool call metadata in the response.
  - AgentMaestro stores the assistant turn and the tool call locally.
  - The tool runner executes the tool outside the provider.
- Tool results:
  - The next request uses `response.create` again.
  - If there is a pending tool result, the bridge sends the trailing tool output items instead of replaying the whole transcript.
  - The provider call chain is preserved with `previous_response_id`.
- After a tool call:
  - The local run history records the assistant call, the tool execution, and the tool result.
  - The follow-up provider turn is a continuation, not a fresh conversation.

## OpenAI HTTP Responses

- Initial request shape:
  - The transport sends `input` items to the Responses API.
  - Messages are converted into provider input items, not passed through as a raw chat transcript.
  - Tool definitions are sent separately as Responses tool definitions.
  - `store=True` is used so the provider can maintain the response chain.
- What the model sees:
  - System messages are included as `message` items with the `system` role.
  - User messages are included as `message` items with the `user` role.
  - Assistant messages are included as `message` items with the `assistant` role.
  - Tool outputs are included as `function_call_output` items that carry the provider `call_id`.
- Tool calls:
  - The provider returns tool call objects in the raw response.
  - AgentMaestro normalizes those tool calls into local `ToolCall` records and local history entries.
- Tool results:
  - When the run resumes after tool execution, the bridge prefers the smallest valid continuation payload.
  - If a previous response chain exists and the pending provider call is known, only the matching tool output items are sent.
  - Otherwise the bridge falls back to the most recent user turn.
- After a tool call:
  - The provider chain continues through `previous_response_id`.
  - The local run keeps the complete message and tool history, even when the provider only receives the minimal continuation slice.

## OpenAI HTTP Chat Completions

- Initial request shape:
  - The transport sends the raw `messages` list to `chat.completions.create`.
  - Tool schemas are normalized into OpenAI-compatible function tools and passed separately.
- What the model sees:
  - The model sees the message history exactly as the chat-completions endpoint expects it.
  - There is no `previous_response_id` chain in this mode.
- Tool calls:
  - The model returns `tool_calls` on the assistant message.
  - AgentMaestro stores the tool call locally and executes the tool outside the provider.
- Tool results:
  - Tool result messages remain part of the normal local message history.
  - The next chat completion call sends the updated history again, without a provider continuation token.
- After a tool call:
  - Continuity comes from the transcript itself, not from an API chain id.

## Gemini HTTP

- Initial request shape:
  - The transport sends a `generateContent` request.
  - System messages are merged into `systemInstruction`.
  - Non-system messages are converted into `contents`.
  - Tool definitions are converted into Gemini function declarations.
- What the model sees:
  - User messages become `user` contents.
  - Assistant messages become `model` contents.
  - Assistant turns with tool calls are represented as `functionCall` parts.
  - Tool outputs are represented as `functionResponse` parts.
- Tool calls:
  - The provider returns tool call data in the candidate response and the raw payload.
  - AgentMaestro normalizes that back into local tool call records.
- Tool results:
  - The follow-up request does not send an OpenAI-style chain token to Gemini.
  - The bridge trims the local history to the latest relevant turn when `previous_response_id` is present.
  - Tool results are serialized as function responses inside the `contents` list.
- After a tool call:
  - The conversation is effectively rebuilt from local history each time, but the request shape is Gemini-native rather than OpenAI-native.

## Tool Result Lifecycle

- The model requests a tool.
- AgentMaestro records the request locally.
- The actual tool runs in ToolRunner or the relevant backend executor.
- The tool result is written back into local history.
- The next provider turn receives the tool result in the provider-specific format:
  - OpenAI Responses and WS use `function_call_output`.
  - Gemini uses `functionResponse`.
  - Chat Completions replays the updated chat transcript.

## Admin Run-Step Links In Condensed Logs

- When `SHOW_CONDENSED_SYSTEM_LOGS=1`, the UI adds an admin link for the relevant `AgentStep` so operators can jump directly from the condensed system message to the raw admin record.
- When `SHOW_CONDENSED_SYSTEM_LOGS=0`, the UI shows the full payload and the admin link is not needed.

## Practical Reading Order

1. Read this file for the transport rules.
2. Read `backend/llm/system_context.py` for the exact system prompt content.
3. Read `backend/runs/services/input_items.py` for how history becomes provider input items.
4. Read the provider implementation for the transport you care about.
5. Use the system chat log mode to inspect either the condensed summary or the exact JSON payload.
