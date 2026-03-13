from llm.services.providers.openai_client import OpenAIClient


def test_messages_to_responses_input_prefers_provider_call_id_for_tool_outputs():
    client = OpenAIClient.__new__(OpenAIClient)

    items = client._messages_to_responses_input(
        [
            {
                "role": "tool",
                "tool_call_id": "local-tool-call-id",
                "provider_call_id": "provider-call-id",
                "content": {"ok": False, "error": {"message": "use git_add"}},
            }
        ]
    )

    assert items == [
        {
            "type": "function_call_output",
            "call_id": "provider-call-id",
            "output": '{"ok": false, "error": {"message": "use git_add"}}',
        }
    ]
