from __future__ import annotations

from llm.services.tool_code import extract_code_like_tool_calls, is_code_like_tool_output


def test_extract_code_like_tool_calls_parses_default_api_remember():
    text = (
        "tool_code\n"
        "print(default_api.remember("
        "scope_type='user', "
        "scope_id='1', "
        "memory_kind='episodic', "
        "content='The user is happy.', "
        "summary='User expressed satisfaction.', "
        "tags=['user_feedback', 'introduction']"
        "))"
    )

    calls = extract_code_like_tool_calls(text, ["remember", "search_memory"])

    assert is_code_like_tool_output(text) is True
    assert len(calls) == 1
    assert calls[0]["name"] == "remember"
    assert calls[0]["arguments"]["scope_type"] == "user"
    assert calls[0]["arguments"]["scope_id"] == "1"
    assert calls[0]["arguments"]["memory_kind"] == "episodic"
    assert calls[0]["arguments"]["summary"] == "User expressed satisfaction."
    assert calls[0]["arguments"]["tags"] == ["user_feedback", "introduction"]


def test_extract_code_like_tool_calls_ignores_disallowed_tool_names():
    text = "tool_code\nprint(default_api.remember(scope_type='user', scope_id='1'))"

    calls = extract_code_like_tool_calls(text, ["search_memory"])

    assert is_code_like_tool_output(text) is True
    assert calls == []


def test_extract_code_like_tool_calls_returns_empty_for_plain_text():
    text = "I remembered that for later."

    calls = extract_code_like_tool_calls(text, ["remember"])

    assert is_code_like_tool_output(text) is False
    assert calls == []
