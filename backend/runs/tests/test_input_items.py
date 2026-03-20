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

