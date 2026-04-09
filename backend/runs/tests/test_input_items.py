from runs.services.input_items import build_input_items, build_ws_request_input_items


def test_build_ws_request_input_items_uses_explicit_provider_call_ids_for_tool_outputs():
    history = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "tool_a", "arguments": {}}]},
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "tool_row_1", "provider_call_id": "call_1"},
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "tool_row_2", "provider_call_id": "call_2"},
    ]

    items = build_ws_request_input_items(
        history,
        previous_response_id="resp_123",
        outstanding_provider_call_ids=["call_1", "call_2"],
        include_system_context=True,
        run_id="run_123",
    )

    assert items
    assert all(item.get("type") == "function_call_output" for item in items)
    assert [item.get("call_id") for item in items] == ["call_1", "call_2"]


def test_build_ws_request_input_items_keeps_artifact_context_when_system_context_is_disabled():
    history = [
        {
            "role": "system",
            "content": "ATTACHED FILE CONTEXT\nFilename: notes.txt\nFull path: C:\\Dev\\AgentMaestro\\backend\\run_artifacts\\run-1\\artifact-1\\notes.txt\nType: FILE\nExtracted content:\nhello world\nInstruction: Use this content directly. Do not infer that only the filename was provided.",
            "kind": "artifact_context",
            "artifact_id": "artifact-1",
            "artifact_path": r"C:\Dev\AgentMaestro\backend\run_artifacts\run-1\artifact-1\notes.txt",
        },
        {"role": "system", "content": "AGENTS bootstrap", "_agentmaestro_system_context": True},
        {"role": "user", "content": "Summarize the attached file test.txt."},
    ]

    items = build_ws_request_input_items(
        history,
        previous_response_id="resp_123",
        include_system_context=False,
        last_user_text="Summarize the attached file test.txt.",
        run_id="run_123",
    )

    assert items[0]["role"] == "system"
    assert items[0]["content"][0]["text"].startswith("ATTACHED FILE CONTEXT")
    assert "Instruction: Use this content directly." in items[0]["content"][0]["text"]
    assert items[1]["role"] == "user"


def test_build_input_items_compacts_large_google_bridge_tool_output():
    history = [
        {
            "role": "tool",
            "tool_call_id": "tool_row_1",
            "provider_call_id": "call_1",
            "tool_name": "google_bridge",
            "content": (
                '{"ok":true,"integration_kind":"google","resource_kind":"gmail","action_kind":"read",'
                '"operation":"list","summary_text":"Returned 50 Gmail messages.","result":'
                '{"messages":[{"id":"msg-1","snippet":"%s"}]}}'
                % ("x" * 8000)
            ),
        }
    ]

    items = build_input_items(history, previous_response_id="resp_123", outstanding_provider_call_id="call_1")

    assert len(items) == 1
    assert items[0]["type"] == "function_call_output"
    assert len(items[0]["output"]) < 6000
    assert "Returned 50 Gmail messages." in items[0]["output"]


def test_build_input_items_compacts_people_bridge_tool_output():
    history = [
        {
            "role": "tool",
            "tool_call_id": "tool_row_1",
            "provider_call_id": "call_1",
            "tool_name": "google_bridge",
            "content": (
                '{"ok":true,"integration_kind":"google","resource_kind":"people","action_kind":"read",'
                '"operation":"list","summary_text":"Returned 2 Google contacts.","result":'
                '{"connections":[{"resourceName":"people/c1","names":[{"displayName":"Scott Kissinger"}],'
                '"emailAddresses":[{"value":"scott@example.com"}]},'
                '{"resourceName":"people/c2","names":[{"displayName":"Scott Contact"}]}],'
                '"nextPageToken":"","nextSyncToken":"sync-1","totalPeople":2,"totalItems":2}}'
            ),
        }
    ]

    items = build_input_items(history, previous_response_id="resp_123", outstanding_provider_call_id="call_1")

    assert len(items) == 1
    assert items[0]["type"] == "function_call_output"
    assert len(items[0]["output"]) < 6000
    assert "Returned 2 Google contacts." in items[0]["output"]

