from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from .event_logger import EventLogger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FileSection(BaseModel):
    ok: bool
    expected: List[str]
    found: List[str]
    missing: List[str]
    extra: List[str]
    notes: List[str]


class GitSection(BaseModel):
    ok: bool
    branch: Optional[str]
    is_detached: bool
    is_clean: bool
    head_commit: Dict[str, str]
    notes: List[str]


class StepStructureSection(BaseModel):
    ok: bool
    score: int
    findings: List[str]
    step_counts: Dict[str, int]
    ordering_ok: bool


class RegressionSection(BaseModel):
    ok: bool
    score: int
    properties: Dict[str, bool]
    notes: List[str]


class VerificationSections(BaseModel):
    files: FileSection
    git: GitSection
    step_structure: StepStructureSection
    regression_value: RegressionSection


class VerificationResult(BaseModel):
    run_id: str
    ok: bool
    autonomy_ok: bool
    hygiene_ok: bool
    overall_ok: bool
    score_overall: int
    sections: VerificationSections
    fail_reasons: List[str]
    warnings: List[str]
    remediation: List[str]
    created_at: str


TEMPLATE_EXPECTED_FILES: Dict[str, List[Tuple[str, Sequence[str]]]] = {
    "todo_cli_v1": [
        ("README.md", ["README.md"]),
        ("pyproject/requirements", ["pyproject.toml", "requirements.txt"]),
        ("implementation notes", ["implementation/notes.txt"]),
    ],
}


def _load_plan(run_root: Path) -> Dict[str, Any]:
    plan_path = run_root / "plans" / "latest.json"
    if not plan_path.exists():
        return {}
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _list_step_reports(run_root: Path) -> List[Path]:
    reports_root = run_root / "step_reports"
    if not reports_root.exists():
        return []
    return [p for p in reports_root.rglob("*.json") if p.is_file()]


def _read_events(run_root: Path) -> List[Dict[str, Any]]:
    events_path = run_root / "events.jsonl"
    if not events_path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _read_run_metadata(run_root: Path) -> Dict[str, Any]:
    metadata_path = run_root / "run_metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_run_metadata(run_root: Path) -> Dict[str, Any]:
    return _read_run_metadata(run_root)


def _collect_expected_files(repo_dir: Path, template_slug: Optional[str]) -> Tuple[FileSection, int]:
    seen_labels: set[str] = set()
    definitions: list[Tuple[str, Sequence[str]]] = []
    for label, options in TEMPLATE_EXPECTED_FILES.get(template_slug or "", []):
        if label in seen_labels:
            continue
        seen_labels.add(label)
        definitions.append((label, options))
    for label, options in [
        ("README.md", ["README.md"]),
        ("pyproject/requirements", ["pyproject.toml", "requirements.txt"]),
    ]:
        if label in seen_labels:
            continue
        seen_labels.add(label)
        definitions.append((label, options))
    repo_dir = repo_dir.resolve()
    found: List[str] = []
    missing: List[str] = []
    notes: List[str] = []
    expected: List[str] = []
    extras: List[str] = []

    seen_paths: List[str] = []
    for label, options in definitions:
        expected.append(label)
        matched = None
        for candidate in options:
            path = repo_dir / candidate
            if path.exists():
                matched = path.relative_to(repo_dir).as_posix()
                break
        if matched:
            found.append(matched)
            seen_paths.append(matched)
        else:
            if label not in missing:
                missing.append(label)
            notes.append(f"Expected {label} not found.")

    # source file presence under conventional dirs
    source_dirs = ["implementation", "src", "app"]
    source_found = False
    for directory in source_dirs:
        folder = repo_dir / directory
        if folder.exists():
            for file in folder.rglob("*"):
                if file.is_file():
                    rel = file.relative_to(repo_dir).as_posix()
                    if rel not in seen_paths:
                        extras.append(rel)
                    found.append(rel)
                    source_found = True
                    break
            if source_found:
                break
    if not source_found:
        missing.append("source file (implementation/src/app)")
        notes.append("No source files detected under implementation/src/app.")

    tests_found = False
    for tests_dir in repo_dir.rglob("tests"):
        if tests_dir.is_dir():
            for file in tests_dir.rglob("*"):
                if file.is_file():
                    rel = file.relative_to(repo_dir).as_posix()
                    extras.append(rel)
                    found.append(rel)
                    tests_found = True
                    break
            if tests_found:
                break
    if not tests_found:
        missing.append("test file under tests/")
        notes.append("No files found under a tests/ directory.")

    score = max(0, 25 - 5 * len(missing))
    ok = len(missing) == 0
    return FileSection(
        ok=ok,
        expected=expected,
        found=list(dict.fromkeys(found)),
        missing=missing,
        extra=list(dict.fromkeys(extras)),
        notes=notes,
    ), score


def _run_git_command(repo_dir: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )


def _inspect_git(repo_dir: Path) -> Tuple[GitSection, int]:
    repo_dir = Path(repo_dir)
    branch = None
    is_detached = False
    branch_notes: List[str] = []
    try:
        proc = _run_git_command(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            if branch == "HEAD":
                is_detached = True
                branch_notes.append("HEAD is detached.")
            elif branch in {"main", "master"}:
                branch_notes.append(f"Branch {branch} is a protected branch.")
        else:
            is_detached = True
            branch_notes.append("Unable to determine branch; HEAD may be detached.")
    except FileNotFoundError:
        branch_notes.append("Git executable unavailable.")

    head_commit = {"hash": "", "subject": ""}
    try:
        proc = _run_git_command(repo_dir, ["log", "-1", "--pretty=format:%h|%s"])
        if proc.returncode == 0 and proc.stdout:
            short_hash, _, subject = proc.stdout.partition("|")
            head_commit = {"hash": short_hash, "subject": subject}
        else:
            branch_notes.append("Unable to read HEAD commit.")
    except FileNotFoundError:
        branch_notes.append("Git executable unavailable.")

    def _filter_run_artifact_lines(lines: List[str]) -> List[str]:
        filtered: List[str] = []
        for entry in lines:
            path_segment = entry[3:].strip() if len(entry) > 3 else entry.strip()
            path_segment = path_segment.split("\t")[-1].strip()
            path_segment = path_segment.split("->")[-1].strip()
            normalized = path_segment.replace("\\", "/")
            if normalized.startswith(".agentmaestro/") or normalized == ".agentmaestro":
                continue
            filtered.append(entry)
        return filtered

    is_clean = False
    cleaned = False
    try:
        proc = _run_git_command(repo_dir, ["status", "--porcelain"])
        if proc.returncode == 0:
            raw = proc.stdout.strip()
            lines = raw.splitlines()
            filtered_lines = _filter_run_artifact_lines(lines)
            is_clean = not bool(filtered_lines)
            if not is_clean:
                branch_notes.append("Working tree has unstaged or untracked changes.")
                cleaned = False
            else:
                cleaned = True
        else:
            branch_notes.append("Git status call failed.")
    except FileNotFoundError:
        branch_notes.append("Git executable unavailable.")

    issues = 0
    ok = True
    if not cleaned:
        ok = False
        issues += 1
    if branch in {"main", "master"}:
        ok = False
        issues += 1
    if is_detached:
        ok = False
        issues += 1
    if branch and not branch.startswith("agent/"):
        branch_notes.append(f"Branch {branch} does not follow agent/ prefix (optional).")

    score = max(0, 25 - 5 * issues)
    return (
        GitSection(
            ok=ok,
            branch=branch,
            is_detached=is_detached,
            is_clean=is_clean,
            head_commit=head_commit,
            notes=branch_notes,
        ),
        score,
    )


def _analyze_step_structure(run_root: Path, plan: Dict[str, Any]) -> Tuple[StepStructureSection, int]:
    milestones = plan.get("milestones", [])
    counts: Dict[str, int] = {}
    findings: List[str] = []
    ordering_ok = True
    has_tests = False
    has_gates = False
    has_commit = False
    commit_idx = -1
    tests_idx = -1
    for idx, milestone in enumerate(milestones):
        steps = milestone.get("steps", [])
        counts[milestone.get("milestone_id", f"milestone_{idx}") or f"milestone_{idx}"] = len(steps)
        for step in steps:
            for call in step.get("tool_calls", []):
                tool = call.get("tool")
                if tool == "test_runner":
                    has_tests = True
                    tests_idx = idx
                if tool in {"format_runner", "lint_runner", "typecheck_runner"}:
                    has_gates = True
                if tool == "git_commit":
                    has_commit = True
                    commit_idx = idx
    if not has_tests:
        findings.append("No test_runner step detected.")
    if not has_gates:
        findings.append("Quality gate tools (format/lint/typecheck) missing.")
    if not has_commit:
        findings.append("No git_commit step found.")
    if commit_idx != len(milestones) - 1:
        ordering_ok = False
        findings.append("Commit milestone is not last.")
    if has_tests and commit_idx >= 0 and tests_idx > commit_idx:
        ordering_ok = False
        findings.append("Tests run after commit.")
    score = max(0, 25 - 5 * len(findings))
    ok = len(findings) == 0
    return StepStructureSection(
        ok=ok,
        score=score,
        findings=findings,
        step_counts=counts,
        ordering_ok=ordering_ok,
    ), score


def _regression_properties(
    run_root: Path, events: List[Dict[str, Any]], step_reports: List[Path], plan: Dict[str, Any]
) -> Tuple[RegressionSection, int]:
    properties: Dict[str, bool] = {}
    readiness_path = run_root / "srs" / "readiness.json"
    readiness_data: Dict[str, Any] = {}
    if readiness_path.exists():
        readiness_data = json.loads(readiness_path.read_text(encoding="utf-8"))
    locked_sections = readiness_data.get("locked_sections", [])
    properties["srs_seeded_and_locked"] = bool(locked_sections)
    properties["plan_generated"] = bool(plan)
    properties["run_started"] = any(evt.get("type") == "RUN_STARTED" for evt in events)
    tool_calls = [evt for evt in events if evt.get("type") == "TOOL_CALLED"]
    properties["tool_calls_executed"] = bool(tool_calls)
    properties["step_report_written"] = bool(step_reports)
    properties["tests_executed"] = any(evt.get("data", {}).get("tool") == "test_runner" for evt in tool_calls)
    properties["gates_executed"] = any(evt.get("type") == "GATES_RUN" for evt in events)
    commit_reports = []
    for report_path in step_reports:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if any(result.get("tool") == "git_commit" for result in payload.get("tool_results", [])):
            commit_reports.append(report_path)
    properties["commit_executed"] = bool(commit_reports)
    properties["run_finalized_ok"] = any(
        evt.get("type") == "RUN_FINALIZED" and evt.get("data", {}).get("status") == "ok" for evt in events
    )
    failing = [k for k, v in properties.items() if not v]
    score = max(0, 25 - 3 * len(failing))
    notes = []
    for key in failing:
        notes.append(f"Missing property: {key}")
    ok = len(failing) == 0
    return RegressionSection(ok=ok, score=score, properties=properties, notes=notes), score


def _evaluate_autonomy(
    files_section: FileSection,
    structure_section: StepStructureSection,
    regression_section: RegressionSection,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    ok = True
    if not files_section.ok:
        ok = False
        reasons.append("Expected files or directories were not produced.")
    if not structure_section.ok:
        ok = False
        reasons.append("Step structure validation reported issues.")
    if not structure_section.ordering_ok:
        ok = False
        reasons.append("Step ordering or commit placement violated expectations.")
    required_properties = {
        "run_finalized_ok": "Run did not finalize successfully.",
        "step_report_written": "No step reports were written.",
        "tool_calls_executed": "No tool calls were recorded.",
        "tests_executed": "Tests did not execute.",
        "gates_executed": "Gates (format/lint/typecheck) did not run.",
        "commit_executed": "Git commit step was not recorded.",
    }
    for key, message in required_properties.items():
        if not regression_section.properties.get(key):
            ok = False
            reasons.append(message)
    reasons.extend(structure_section.findings)
    # Keep order while deduping
    reasons = list(dict.fromkeys(reasons))
    return ok, reasons


def _assess_hygiene(git_section: GitSection) -> Tuple[bool, List[str], List[str]]:
    warnings = list(dict.fromkeys(git_section.notes or []))
    remediation: List[str] = []
    ok = True

    def add_warning(reason: str, message: str) -> None:
        nonlocal ok
        ok = False
        if reason not in warnings:
            warnings.append(reason)
        remediation.append(message)

    if not git_section.is_clean:
        add_warning(
            "Working tree has unstaged or untracked changes.",
            "Clean or stash changes (git status + git add/commit or git reset --hard) before rerunning.",
        )
    if git_section.is_detached:
        add_warning(
            "HEAD is detached.",
            "Checkout a branch (git checkout <branch> or git checkout -b agent/<name>) before rerunning.",
        )
    if git_section.branch in {"main", "master"}:
        add_warning(
            f"Branch {git_section.branch} is a protected branch.",
            "Create/checkout a feature branch (git checkout -b agent/<name>), then rerun.",
        )

    return ok, warnings, remediation


def _render_markdown(result: VerificationResult) -> str:
    lines: List[str] = [
        f"# Verification Report for {result.run_id}",
        "",
        f"- **Overall score:** {result.score_overall}/100",
        f"- **Overall status:** {'PASS' if result.overall_ok else 'FAIL'}",
        f"- **Autonomy:** {'PASS' if result.autonomy_ok else 'FAIL'}",
        f"- **Hygiene:** {'PASS' if result.hygiene_ok else 'WARN'}",
        "",
        "## Files",
        f"- OK: {result.sections.files.ok}",
        f"- Expected: {', '.join(result.sections.files.expected) or 'None'}",
        f"- Missing: {', '.join(result.sections.files.missing) or 'None'}",
        f"- Found: {', '.join(result.sections.files.found) or 'None'}",
    ]
    if result.sections.files.notes:
        lines.append("### File Notes")
        lines.extend(f"- {note}" for note in result.sections.files.notes)
    lines.extend(
        [
            "",
            "## Git",
            f"- Branch: {result.sections.git.branch or 'unknown'}",
            f"- Detached: {result.sections.git.is_detached}",
            f"- Clean: {result.sections.git.is_clean}",
            f"- HEAD: {result.sections.git.head_commit.get('hash')} – {result.sections.git.head_commit.get('subject')}",
        ]
    )
    if result.sections.git.notes:
        lines.append("### Git Notes")
        lines.extend(f"- {note}" for note in result.sections.git.notes)
    lines.extend(
        [
            "",
            "## Step Structure",
            f"- Score: {result.sections.step_structure.score}",
            f"- Ordering ok: {result.sections.step_structure.ordering_ok}",
        ]
    )
    if result.sections.step_structure.findings:
        lines.append("### Findings")
        lines.extend(f"- {finding}" for finding in result.sections.step_structure.findings)
    lines.extend(
        [
            "",
            "## Regression Properties",
            f"- Score: {result.sections.regression_value.score}",
            "- Properties:",
        ]
    )
    for key, value in result.sections.regression_value.properties.items():
        lines.append(f"  - {key}: {value}")
    if result.sections.regression_value.notes:
        lines.append("### Regression Notes")
        lines.extend(f"- {note}" for note in result.sections.regression_value.notes)
    if result.fail_reasons:
        lines.extend(["", "## Fail Reasons"])
        lines.extend(f"- {reason}" for reason in result.fail_reasons)
    if result.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.remediation:
        lines.extend(["", "## Remediation"])
        lines.extend(f"- {action}" for action in result.remediation)
    lines.append("")
    lines.append(f"_Generated at {result.created_at}_")
    return "\n".join(lines)


def run_post_run_verification(
    run_id: str,
    repo_dir: str,
    run_root: Path,
    template_slug: Optional[str] = None,
    event_logger: EventLogger | None = None,
) -> VerificationResult:
    repo_path = Path(repo_dir).resolve()
    run_root = run_root.resolve()
    sections_dir = run_root / "verify"
    sections_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_run_metadata(run_root)
    template = template_slug or metadata.get("template")
    plan = _load_plan(run_root)
    events = _read_events(run_root)
    step_reports = _list_step_reports(run_root)
    files_section, files_score = _collect_expected_files(repo_path, template)
    git_section, git_score = _inspect_git(repo_path)
    structure_section, structure_score = _analyze_step_structure(run_root, plan)
    regression_section, regression_score = _regression_properties(run_root, events, step_reports, plan)
    total_score = min(100, files_score + git_score + structure_score + regression_score)
    autonomy_ok, fail_reasons = _evaluate_autonomy(files_section, structure_section, regression_section)
    hygiene_ok, warnings, remediation = _assess_hygiene(git_section)
    overall_ok = autonomy_ok
    result = VerificationResult(
        run_id=run_id,
        ok=overall_ok,
        autonomy_ok=autonomy_ok,
        hygiene_ok=hygiene_ok,
        overall_ok=overall_ok,
        score_overall=total_score,
        sections=VerificationSections(
            files=files_section,
            git=git_section,
            step_structure=structure_section,
            regression_value=regression_section,
        ),
        fail_reasons=fail_reasons,
        warnings=warnings,
        remediation=remediation,
        created_at=_now_iso(),
    )
    json_path = sections_dir / "verification.json"
    md_path = sections_dir / "verification.md"
    json_path.write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    logger = event_logger or EventLogger(run_root)
    logger.log(
        "VERIFICATION_WRITTEN",
        {
            "run_id": run_id,
            "ok": result.overall_ok,
            "autonomy_ok": result.autonomy_ok,
            "hygiene_ok": result.hygiene_ok,
            "score_overall": result.score_overall,
            "fail_reasons": result.fail_reasons,
            "warnings": result.warnings,
            "remediation": result.remediation,
            "verification_path_json": str(json_path.relative_to(run_root)),
            "verification_path_md": str(md_path.relative_to(run_root)),
        },
    )
    return result
