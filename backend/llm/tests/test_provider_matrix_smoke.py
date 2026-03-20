from __future__ import annotations

from llm.management.commands.llm_provider_matrix_smoke import (
    DEFAULT_CONFIG_PATH,
    ScenarioOutcome,
    _load_matrix_config,
    _score_direct_reply,
    _score_tool_lookup,
    _speed_score,
)


def test_provider_matrix_config_loads_defaults():
    config = _load_matrix_config(DEFAULT_CONFIG_PATH)
    assert len(config["targets"]) == 6
    assert len(config["scenarios"]) == 2
    assert config["rating"]["speed_weight"] == 0.4
    assert config["rating"]["quality_weight"] == 0.6


def test_speed_score_normalizes_against_target():
    assert _speed_score(1000, 1000) == 100.0
    assert _speed_score(2000, 1000) == 50.0


def test_direct_reply_scoring_ignores_punctuation():
    outcome = ScenarioOutcome(
        name="direct_reply",
        kind="chat",
        status="completed",
        elapsed_ms=1000,
        speed_score=100.0,
        quality_score=0.0,
        overall_score=0.0,
        text="matrix ok.",
    )
    score, notes = _score_direct_reply(outcome, "matrix ok")
    assert score == 100.0
    assert notes == []


def test_tool_lookup_scoring_rewards_tools_and_text():
    outcome = ScenarioOutcome(
        name="repo_lookup",
        kind="tool",
        status="completed",
        elapsed_ms=1000,
        speed_score=100.0,
        quality_score=0.0,
        overall_score=0.0,
        text="normalize_provider_for_model lives in agents/utils.py",
        tool_names=["search_code", "file_read"],
    )
    score, notes = _score_tool_lookup(
        outcome,
        required_tools=["search_code", "file_read"],
        expected_contains=["normalize_provider_for_model", "agents/utils.py"],
    )
    assert score == 100.0
    assert notes == []
