from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from toolrunner.app.run_manager import RunContext

READINESS_SECTION_POINTS: dict[str, int] = {
    "project_summary": 15,
    "goals_non_goals": 15,
    "functional_requirements": 25,
    "acceptance_criteria": 25,
    "risks_assumptions": 10,
    "interfaces": 10,
}

READINESS_CHECKS: list[tuple[str, str]] = [
    ("project_summary", "Project Summary"),
    ("goals_non_goals", "Goals & Non-Goals"),
    ("functional_requirements", "Functional Requirements"),
    ("acceptance_criteria", "Acceptance Criteria"),
    ("risks_assumptions", "Risks & Assumptions"),
]

FUNCTIONAL_REQUIREMENTS_BULLET_THRESHOLD = 3
ACCEPTANCE_CRITERIA_BULLET_THRESHOLD = 2
READINESS_SCORE_THRESHOLD = 60


def readiness_path(run_root: Path) -> Path:
    path = run_root / "srs" / "readiness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _count_bullets(content: str) -> int:
    return sum(1 for line in content.splitlines() if line.strip().startswith("-"))


def load_readiness(run_root: Path) -> dict[str, Any] | None:
    path = readiness_path(run_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_readiness(run_root: Path, payload: dict[str, Any]) -> None:
    readiness_path(run_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def compute_readiness(context: "RunContext") -> dict[str, Any]:
    builder = context.srs_builder
    locked = builder.locked_sections
    score = 0
    for section_id, points in READINESS_SECTION_POINTS.items():
        if section_id in locked:
            score += points
    checks: dict[str, bool] = {}
    missing: list[str] = []
    for section_id, label in READINESS_CHECKS:
        is_locked = section_id in locked
        checks[f"{section_id}_locked"] = is_locked
        if not is_locked:
            missing.append(f"{label} is not locked yet.")
    functional_content = locked.get("functional_requirements", {}).get("content", "")
    acceptance_content = locked.get("acceptance_criteria", {}).get("content", "")
    functional_bullets = _count_bullets(functional_content)
    acceptance_bullets = _count_bullets(acceptance_content)
    warnings: list[str] = []
    if checks.get("functional_requirements_locked") and functional_bullets < FUNCTIONAL_REQUIREMENTS_BULLET_THRESHOLD:
        warnings.append("Functional requirements too sparse.")
        score -= 10
    if checks.get("acceptance_criteria_locked") and acceptance_bullets < ACCEPTANCE_CRITERIA_BULLET_THRESHOLD:
        warnings.append("Acceptance criteria too sparse.")
        score -= 10
    score = max(0, min(100, score))
    payload = {
        "score": score,
        "locked_sections": list(locked.keys()),
        "checks": {
            "project_summary_locked": checks.get("project_summary_locked", False),
            "goals_locked": checks.get("goals_non_goals_locked", False),
            "functional_requirements_locked": checks.get("functional_requirements_locked", False),
            "acceptance_criteria_locked": checks.get("acceptance_criteria_locked", False),
            "risks_locked": checks.get("risks_assumptions_locked", False),
        },
        "counts": {
            "functional_requirements_bullets": functional_bullets,
            "acceptance_criteria_bullets": acceptance_bullets,
        },
        "missing": missing,
        "warnings": warnings,
    }
    _write_readiness(context.run_root, payload)
    context.event_logger.log("SRS_READINESS_COMPUTED", {"run_id": context.run_id, "score": score})
    return payload


def ensure_readiness(context: "RunContext") -> dict[str, Any]:
    existing = load_readiness(context.run_root)
    if existing:
        return existing
    return compute_readiness(context)
