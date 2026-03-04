import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toolrunner.app.main import app, run_manager
from toolrunner.app.verify import run_post_run_verification


def _init_git_repo(path: Path, *, branch: str = "agent/trial-trivial-verification") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    (path / "baseline.txt").write_text("baseline")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=path, check=True, capture_output=True)
    return path


def _write_plan(run_root: Path, run_id: str) -> None:
    plan = {
        "schema_version": "1.0",
        "plan_id": f"plan-{run_id}",
        "run_id": run_id,
        "created_at": "2026-01-01T00:00:00Z",
        "goal": "sample verification run",
        "assumptions": [],
        "complete": True,
        "milestones": [
            {"milestone_id": "scaffold", "title": "scaffold", "steps": [{"step_id": "SC-001", "intent": "scaffold", "tool_calls": [{"call_id": "SC-001-1", "tool": "file_write", "args": {"path": "README.md"}}]}]},
            {"milestone_id": "tests", "title": "tests", "steps": [{"step_id": "TS-001", "intent": "run tests", "tool_calls": [{"call_id": "TS-001-1", "tool": "test_runner", "args": {"kind": "shell"}}]}]},
            {"milestone_id": "gates", "title": "gates", "steps": [{"step_id": "GT-001", "intent": "gates", "tool_calls": [{"call_id": "GT-001-1", "tool": "format_runner", "args": {}}, {"call_id": "GT-001-2", "tool": "lint_runner", "args": {}}, {"call_id": "GT-001-3", "tool": "typecheck_runner", "args": {}}]}]},
            {"milestone_id": "commit", "title": "commit", "steps": [{"step_id": "CM-001", "intent": "commit", "tool_calls": [{"call_id": "CM-001-1", "tool": "git_status", "args": {}}, {"call_id": "CM-001-2", "tool": "git_add", "args": {"paths": ["README.md"]}}, {"call_id": "CM-001-3", "tool": "git_commit", "args": {"message": "feat: verify"}}]}]},
        ],
    }
    plan_dir = run_root / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "latest.json").write_text(json.dumps(plan), encoding="utf-8")


def _write_step_report(run_root: Path, milestone: str, step: str, tools: list[str]) -> None:
    report_dir = run_root / "step_reports" / milestone
    report_dir.mkdir(parents=True, exist_ok=True)
    tool_results = []
    for idx, tool in enumerate(tools, start=1):
        tool_results.append({"call_id": f"{step}-{idx}", "tool": tool, "ok": True, "error": None, "result": {"message": "ok"}})
    report = {
        "schema_version": "1.0",
        "run_id": run_root.name,
        "plan_id": f"plan-{run_root.name}",
        "milestone_id": milestone,
        "step_id": step,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "status": "ok",
        "tool_results": tool_results,
        "repo_state": {"branch": "agent/trial-trivial-verification/trial-trivial", "head_oid": "abc", "is_clean": True, "changed_files": []},
        "failure_count": 0,
        "cycle_index": 1,
        "verification": {"overall_pass": True, "gates": []},
    }
    report_path = report_dir / f"{step}.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")


def _write_events(run_root: Path, run_id: str) -> None:
    events = [
        {"id": 1, "type": "RUN_CREATED", "data": {"run_id": run_id, "slug": "trial-trivial"}},
        {"id": 2, "type": "RUN_STARTED", "data": {"run_id": run_id, "branch": "agent/trial-trivial-verification/trial-trivial"}},
        {"id": 3, "type": "TOOL_CALLED", "data": {"tool": "test_runner"}},
        {"id": 4, "type": "GATES_RUN", "data": {"verification": {"overall_pass": True}}},
        {"id": 5, "type": "STEP_REPORT_WRITTEN", "data": {"step": "TS-001"}},
        {"id": 6, "type": "RUN_FINALIZED", "data": {"status": "ok", "reason": "all milestones satisfied"}},
    ]
    path = run_root / "events.jsonl"
    path.write_text("\n".join(json.dumps(evt) for evt in events), encoding="utf-8")


def _write_srs(run_root: Path) -> None:
    srs_dir = run_root / "srs"
    srs_dir.mkdir(parents=True, exist_ok=True)
    (srs_dir / "SRS.md").write_text("## Project Summary\n", encoding="utf-8")
    (srs_dir / "SRS.lock.json").write_text("{}", encoding="utf-8")
    readiness = {"score": 100, "locked_sections": ["project_summary"], "missing": [], "warnings": []}
    (srs_dir / "readiness.json").write_text(json.dumps(readiness), encoding="utf-8")


def _write_metadata(run_root: Path, template: str = "todo_cli_v1") -> None:
    metadata = {"template": template, "created_at": "2026-01-01T00:00:00Z"}
    (run_root / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def _populate_expected_repo_files(repo: Path) -> None:
    (repo / "README.md").write_text("## Title\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "implementation").mkdir(exist_ok=True)
    (repo / "implementation" / "notes.txt").write_text("notes", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add expected files"], cwd=repo, check=True, capture_output=True)


def _prepare_verification_artifacts(run_root: Path, run_id: str, *, include_step_reports: bool = True) -> None:
    _write_plan(run_root, run_id)
    if include_step_reports:
        _write_step_report(run_root, "scaffold", "SC-001", ["file_write"])
        _write_step_report(run_root, "tests", "TS-001", ["test_runner"])
        _write_step_report(run_root, "gates", "GT-001", ["format_runner", "lint_runner", "typecheck_runner"])
        _write_step_report(run_root, "commit", "CM-001", ["git_commit"])
    _write_events(run_root, run_id)
    _write_srs(run_root)
    _write_metadata(run_root)


@pytest.mark.parametrize("template", ["todo_cli_v1"])
def test_autonomy_pass_hygiene_warn(tmp_path: Path, template: str):
    repo = _init_git_repo(tmp_path / "repo")
    run_id = "trial-trivial-verification"
    run_root = repo / ".agentmaestro" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _populate_expected_repo_files(repo)
    (repo / "scratch.txt").write_text("dirty", encoding="utf-8")
    _prepare_verification_artifacts(run_root, run_id)
    result = run_post_run_verification(run_id, str(repo), run_root, template_slug=template)
    assert result.overall_ok
    assert result.autonomy_ok
    assert not result.hygiene_ok
    assert any("working tree" in warning.lower() for warning in result.warnings)
    assert any("Clean or stash" in remediation for remediation in result.remediation)
    md_content = (run_root / "verify" / "verification.md").read_text(encoding="utf-8")
    assert "- **Hygiene:** WARN" in md_content
    assert (run_root / "verify" / "verification.json").exists()
    assert (run_root / "verify" / "verification.md").exists()


def test_autonomy_fail_even_if_git_clean(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo_clean")
    run_id = "trial-trivial-autonomy-fail"
    run_root = repo / ".agentmaestro" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _populate_expected_repo_files(repo)
    _prepare_verification_artifacts(run_root, run_id, include_step_reports=False)
    result = run_post_run_verification(run_id, str(repo), run_root)
    assert not result.overall_ok
    assert not result.autonomy_ok
    assert result.hygiene_ok
    assert any("step reports" in reason.lower() for reason in result.fail_reasons)
    assert (run_root / "verify" / "verification.json").exists()
    assert (run_root / "verify" / "verification.md").exists()


@pytest.mark.parametrize(
    "scenario,modifier,expected",
    [
        ("dirty", lambda repo: (repo / "scratch.txt").write_text("dirty", encoding="utf-8"), "working tree"),
        (
            "detached",
            lambda repo: subprocess.run(["git", "checkout", "--detach", "HEAD"], cwd=repo, check=True, capture_output=True),
            "head is detached",
        ),
        (
            "main",
            lambda repo: subprocess.run(["git", "checkout", "-B", "main"], cwd=repo, check=True, capture_output=True),
            "protected branch",
        ),
    ],
)
def test_hygiene_pass_requires_clean_non_main_non_detached(tmp_path: Path, scenario: str, modifier, expected: str):
    repo = _init_git_repo(tmp_path / f"repo_{scenario}")
    run_id = f"trial-trivial-hygiene-{scenario}"
    run_root = repo / ".agentmaestro" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _populate_expected_repo_files(repo)
    _prepare_verification_artifacts(run_root, run_id)
    modifier(repo)
    result = run_post_run_verification(run_id, str(repo), run_root)
    assert result.autonomy_ok
    assert not result.hygiene_ok
    assert any(expected in warning.lower() for warning in result.warnings)
    assert (run_root / "verify" / "verification.md").read_text(encoding="utf-8").count("- **Hygiene:** WARN") == 1


def test_verification_detects_missing_files_and_main_branch(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo_len", branch="main")
    run_id = "trial-trivial-verification-main"
    run_root = repo / ".agentmaestro" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _write_plan(run_root, run_id)
    _write_step_report(run_root, "scaffold", "SC-001", ["file_write"])
    _write_events(run_root, run_id)
    _write_srs(run_root)
    _write_metadata(run_root, template="todo_cli_v1")
    dirty_file = repo / "scratch.txt"
    dirty_file.write_text("dirty", encoding="utf-8")
    result = run_post_run_verification(run_id, str(repo), run_root)
    assert not result.ok
    assert not result.sections.files.ok
    assert "source file" in " ".join(result.sections.files.missing).lower() or "tests/" in " ".join(
        result.sections.files.missing
    ).lower()
    assert not result.sections.git.ok


def test_verification_endpoints(tmp_path: Path):
    client = TestClient(app)
    context = run_manager.create_run("verify-endpoint")
    verify_dir = context.run_root / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    expected = {"run_id": context.run_id, "ok": True, "score_overall": 50, "sections": {}}
    (verify_dir / "verification.json").write_text(json.dumps(expected), encoding="utf-8")
    (verify_dir / "verification.md").write_text("# Verification", encoding="utf-8")
    resp = client.get(f"/v1/runs/{context.run_id}/verify")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == context.run_id
    md_resp = client.get(f"/v1/runs/{context.run_id}/verify/md")
    assert md_resp.status_code == 200
    assert md_resp.json()["content"].startswith("# Verification")
