from runs.services.input_items import build_ws_request_input_items


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

