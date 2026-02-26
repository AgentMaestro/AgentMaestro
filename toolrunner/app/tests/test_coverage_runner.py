import json
from pathlib import Path

from fastapi.responses import JSONResponse

from toolrunner.app.models import CoverageArgs
from toolrunner.app.tools import coverage_runner as coverage_module
from toolrunner.app.tools.coverage_runner import run_coverage


def _fake_coverage_json(path: Path):
    data = {
        "totals": {"percent_covered": 78.4},
        "files": {
            "app/services/foo.py": {"percent_covered": 62.1},
            "toolrunner/app/tools/run_command.py": {"percent_covered": 90.5},
        },
    }
    path.write_text(json.dumps(data))
    return data


def test_coverage_runner_pytest(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        calls.append(run_args.cmd)
        if len(calls) == 1:
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "result": {
                        "exit_code": 0,
                        "duration_ms": 10,
                        "timed_out": False,
                        "stdout": "pytest output",
                        "stderr": "",
                        "stdout_truncated": False,
                        "stderr_truncated": False,
                    },
                },
            )
        coverage_file = tmp_path / "coverage.json"
        _fake_coverage_json(coverage_file)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 5,
                    "timed_out": False,
                    "stdout": "coverage json",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(coverage_module, "run_command", fake_run_command)
    args = CoverageArgs(kind="pytest_coverage", cwd=".")
    response = run_coverage(tmp_path, args)
    payload = json.loads(response.body)
    assert payload["ok"]
    result = payload["result"]
    assert result["total_percent"] == 78.4
    assert {"path": "app/services/foo.py", "percent": 62.1} in result["files"]
    assert calls[0][:3] == ["python", "-m", "coverage"]
    assert calls[1][:3] == ["python", "-m", "coverage"]
    assert result["stdout"] == "pytest output"
    assert result["coverage_stdout"] == "coverage json"
    assert "coverage_json_path" in result


def test_coverage_runner_path_escape(tmp_path: Path):
    response = run_coverage(tmp_path, CoverageArgs(kind="pytest_coverage", cwd="../outside"))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("PATH_OUTSIDE_WORKSPACE")


def test_coverage_runner_missing_json(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_run_command(run_dir, run_args):
        calls.append(run_args.cmd)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "result": {
                    "exit_code": 0,
                    "duration_ms": 5,
                    "timed_out": False,
                    "stdout": "pytest run",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                },
            },
        )

    monkeypatch.setattr(coverage_module, "run_command", fake_run_command)
    response = run_coverage(tmp_path, CoverageArgs(kind="pytest_coverage"))
    payload = json.loads(response.body)
    assert not payload["ok"]
    assert payload["error"]["code"].endswith("NOT_FOUND")
