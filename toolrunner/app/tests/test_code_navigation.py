import json
from pathlib import Path

from toolrunner.app.models import (
    FindReferencesArgs,
    FindSymbolArgs,
    JumpToSymbolArgs,
    ListSymbolsArgs,
    SearchFilesArgs,
)
from toolrunner.app.tools.code_navigation import (
    find_references,
    find_symbol,
    jump_to_symbol,
    list_symbols,
    search_files,
)


def _payload(response):
    return json.loads(response.body)


def test_search_files_finds_files_and_directories(tmp_path: Path):
    (tmp_path / "backend" / "controllers").mkdir(parents=True)
    (tmp_path / "backend" / "controllers" / "main.py").write_text("print('hi')\n")
    (tmp_path / "backend" / "controllers" / "notes.md").write_text("# docs\n")

    response = search_files(
        tmp_path,
        SearchFilesArgs(query="controllers", include_globs=["**/*"], max_results=10),
    )
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    paths = [entry["path"] for entry in result["matches"]]
    assert "backend/controllers" in paths
    assert "backend/controllers/main.py" in paths


def test_search_files_compact_returns_standardized_items(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "code_navigation.py").write_text("print('hi')\n")

    response = search_files(
        tmp_path,
        SearchFilesArgs(query="code_navigation.py", include_globs=["**/*.py"], max_results=10, compact=True),
    )
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["tool"] == "search_files"
    assert result["compact"] is True
    assert result["query"] == "code_navigation.py"
    assert result["scope"]["scope"] == "."
    assert result["returned_count"] == 1
    assert result["max_results_used"] == 10
    assert result["selection"] is None
    assert result["selection_excerpt"] is None
    assert result["items"][0]["path"] == "backend/code_navigation.py"
    assert result["items"][0]["name"] == "code_navigation.py"
    assert result["items"][0]["kind"] == "file"


def test_search_files_reports_allowed_roots_on_scope_rejection(tmp_path: Path):
    response = search_files(
        tmp_path,
        SearchFilesArgs(
            query="code_navigation.py",
            scope=".",
            absolute_root="C:/Windows",
            include_globs=["**/*.py"],
            max_results=10,
            compact=True,
        ),
    )
    payload = _payload(response)
    assert not payload["ok"]
    assert payload["error"]["code"] == "tool_runner.PATH_NOT_ALLOWED"
    assert "absolute path is outside allowed roots" in payload["error"]["message"]
    assert "allowed_roots=" in payload["error"]["message"]


def test_list_symbols_returns_python_symbols(tmp_path: Path):
    module = tmp_path / "sample.py"
    module.write_text(
        "\n".join(
            [
                '"""Sample module."""',
                "",
                "CONSTANT = 1",
                "",
                "class SampleService:",
                "    def run(self):",
                "        return CONSTANT",
                "",
                "def helper():",
                "    return SampleService()",
            ]
        )
    )

    response = list_symbols(tmp_path, ListSymbolsArgs(scope=".", include_docstrings=True))
    payload = _payload(response)
    assert payload["ok"]
    entries = payload["result"]["entries"]
    assert entries[0]["path"] == "sample.py"
    symbols = entries[0]["symbols"]
    names = {symbol["name"] for symbol in symbols}
    assert "SampleService" in names
    assert "helper" in names


def test_list_symbols_compact_adds_flat_items(tmp_path: Path):
    module = tmp_path / "sample.py"
    module.write_text(
        "\n".join(
            [
                "class SampleService:",
                "    def run(self):",
                "        return 1",
            ]
        )
    )

    response = list_symbols(tmp_path, ListSymbolsArgs(scope=".", include_docstrings=False, compact=True))
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["tool"] == "list_symbols"
    assert result["compact"] is True
    assert result["query"] == "."
    assert result["scope"]["scope"] == "."
    assert result["returned_count"] >= 1
    assert result["max_results_used"] == 100
    assert result["selection"] is None
    assert result["selection_excerpt"] is None
    items = result["items"]
    assert any(item["name"] == "SampleService" for item in items)
    assert any(item["kind"] == "class" for item in items)
    sample = next(item for item in items if item["name"] == "SampleService")
    assert sample["container"]
    assert sample["signature"]


def test_find_symbol_exact_match(tmp_path: Path):
    module = tmp_path / "services.py"
    module.write_text(
        "\n".join(
            [
                "class ExampleService:",
                "    def run(self):",
                "        return 'ok'",
            ]
        )
    )

    response = find_symbol(tmp_path, FindSymbolArgs(symbol_name="ExampleService", scope=".", kind="class"))
    payload = _payload(response)
    assert payload["ok"]
    matches = payload["result"]["matches"]
    assert matches[0]["name"] == "ExampleService"
    assert matches[0]["kind"] == "class"
    assert payload["result"]["items"][0]["name"] == "ExampleService"


def test_find_symbol_compact_uses_shared_envelope(tmp_path: Path):
    module = tmp_path / "services.py"
    module.write_text(
        "\n".join(
            [
                "class ExampleService:",
                "    def run(self):",
                "        return 'ok'",
            ]
        )
    )

    response = find_symbol(
        tmp_path,
        FindSymbolArgs(symbol_name="ExampleService", scope=".", kind="class", compact=True),
    )
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["tool"] == "find_symbol"
    assert result["compact"] is True
    assert result["query"] == "ExampleService"
    assert result["scope"]["scope"] == "."
    assert result["returned_count"] == 1
    assert result["max_results_used"] == 20
    assert result["selection"]["name"] == "ExampleService"
    assert result["selection"]["container"]
    assert result["selection"]["signature"]
    assert "qualified_name" not in result["selection"]
    assert "language" not in result["selection"]
    assert result["selection_excerpt"]
    assert result["selection_excerpt"].splitlines()[0].startswith("1:")
    assert result["items"][0]["name"] == "ExampleService"
    assert result["items"][0]["container"]
    assert result["items"][0]["signature"]


def test_find_references_finds_calls(tmp_path: Path):
    module = tmp_path / "calls.py"
    module.write_text(
        "\n".join(
            [
                "def target():",
                "    return 1",
                "",
                "def caller():",
                "    return target()",
            ]
        )
    )

    response = find_references(
        tmp_path,
        FindReferencesArgs(symbol="target", scope=".", include_declarations=True, context_lines=2),
    )
    payload = _payload(response)
    assert payload["ok"]
    matches = payload["result"]["matches"]
    kinds = {match["kind"] for match in matches}
    assert "call" in kinds or "usage" in kinds
    assert payload["result"]["items"][0]["path"] == "calls.py"


def test_find_references_compact_uses_shared_envelope(tmp_path: Path):
    module = tmp_path / "calls.py"
    module.write_text(
        "\n".join(
            [
                "def target():",
                "    return 1",
                "",
                "def caller():",
                "    return target()",
            ]
        )
    )

    response = find_references(
        tmp_path,
        FindReferencesArgs(symbol="target", scope=".", include_declarations=True, context_lines=2, compact=True),
    )
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["tool"] == "find_references"
    assert result["compact"] is True
    assert result["query"] == "target"
    assert result["scope"]["scope"] == "."
    assert result["returned_count"] >= 1
    assert result["max_results_used"] == 50
    assert result["selection"] is not None
    assert "excerpt" not in result["selection"]
    assert result["selection_excerpt"]
    assert result["selection_excerpt"].splitlines()[0].startswith("1:")
    assert result["items"][0]["path"] == "calls.py"
    assert result["items"][0]["excerpt"].splitlines()[0].startswith("1:")


def test_jump_to_symbol_returns_excerpt(tmp_path: Path):
    module = tmp_path / "jump.py"
    module.write_text(
        "\n".join(
            [
                "class JumpTarget:",
                "    def run(self):",
                "        return 'ok'",
                "",
                "def helper():",
                "    return JumpTarget()",
            ]
        )
    )

    response = jump_to_symbol(tmp_path, JumpToSymbolArgs(symbol="JumpTarget", scope=".", kind="class"))
    payload = _payload(response)
    assert payload["ok"]
    excerpt = payload["result"]["excerpt"]
    assert excerpt["path"] == "jump.py"
    assert excerpt["lines"][0]["text"] == "class JumpTarget:"
    assert payload["result"]["items"][0]["name"] == "JumpTarget"


def test_jump_to_symbol_compact_uses_shared_envelope(tmp_path: Path):
    module = tmp_path / "jump.py"
    module.write_text(
        "\n".join(
            [
                "class JumpTarget:",
                "    def run(self):",
                "        return 'ok'",
                "",
                "def helper():",
                "    return JumpTarget()",
            ]
        )
    )

    response = jump_to_symbol(
        tmp_path,
        JumpToSymbolArgs(symbol="JumpTarget", scope=".", kind="class", compact=True),
    )
    payload = _payload(response)
    assert payload["ok"]
    result = payload["result"]
    assert result["tool"] == "jump_to_symbol"
    assert result["compact"] is True
    assert result["query"] == "JumpTarget"
    assert result["scope"]["scope"] == "."
    assert result["returned_count"] >= 1
    assert result["max_results_used"] == 5
    assert result["selection"]["name"] == "JumpTarget"
    assert result["selection"]["container"]
    assert result["selection"]["signature"]
    assert "excerpt" not in result["selection"]
    assert result["selection_excerpt"]
    assert result["selection_excerpt"].splitlines()[0].startswith("1:")
    assert result["items"][0]["name"] == "JumpTarget"
    assert result["items"][0]["container"]
    assert result["items"][0]["signature"]
    assert result["items"][0]["excerpt"].splitlines()[0].startswith("1:")


def test_find_symbol_accepts_legacy_name_field(tmp_path: Path):
    module = tmp_path / "legacy.py"
    module.write_text("class LegacyName:\n    pass\n")

    response = find_symbol(tmp_path, FindSymbolArgs(symbol_name="LegacyName", scope=".", kind="class"))
    payload = _payload(response)
    assert payload["ok"]
    matches = payload["result"]["matches"]
    assert matches[0]["name"] == "LegacyName"
