import json

from pathlib import Path

from toolrunner.app.models import SearchCodeArgs
from toolrunner.app.tools.search_code import list_search_code


def _payload(response):
    return json.loads(response.body)


def test_search_code_literal(tmp_path: Path):
    file = tmp_path / "logger.py"
    file.write_text(
        "\n".join(
            [
                "import logging",
                "",
                "logger = get_logger()",
                "logger.info('ready')",
                "",
                "if get_logger('alpha') is not None:",
            ]
        )
    )
    args = SearchCodeArgs(
        query="get_logger",
        include_globs=["**/*.py"],
    )
    response = list_search_code(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["stats"]["files_scanned"] == 1
    assert result["stats"]["total_matches"] == 2
    assert result["stats"]["files_with_matches"] == 1
    match_entry = result["matches"][0]
    assert match_entry["path"] == "logger.py"
    assert match_entry["match_count"] == 2
    snippets = match_entry["snippets"]
    assert len(snippets) == 2
    first = snippets[0]
    assert first["line_text"] == "logger = get_logger()"
    assert "import logging" in first["context_before"]
    assert "logger.info('ready')" in first["context_after"]


def test_search_code_truncated_max_results(tmp_path: Path):
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("match_term\n")
    args = SearchCodeArgs(query="match_term", include_globs=["**/*.py"], max_results=2)
    response = list_search_code(tmp_path, args)
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["truncated"] is True
    assert result["stats"]["total_matches"] == 2
    assert result["stats"]["files_with_matches"] == 2
    matches = result["matches"]
    assert len(matches) == 2
    assert matches[0]["path"] == "a.py"
    assert matches[1]["path"] == "b.py"


def test_search_code_invalid_regex(tmp_path: Path):
    response = list_search_code(tmp_path, SearchCodeArgs(query="(unclosed", is_regex=True))
    payload = _payload(response)
    assert payload["ok"] is False
    assert payload["error"]["code"].endswith("INVALID_ARGUMENT")
    assert "unclosed" in payload["error"]["details"]["query"]
