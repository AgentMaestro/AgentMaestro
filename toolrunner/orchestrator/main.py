from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

from toolrunner.app.orchestrator import orchestrate
from toolrunner.app.schemas import validate_run_charter


def _slug_from_run_id(run_id: str) -> str:
    candidate = "".join(
        ch if ch.isalnum() or ch in "._-" else "-" for ch in run_id.lower()
    )
    if not candidate or not candidate[0].isalpha():
        candidate = f"a{candidate}"
    return candidate[:64]


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_charter(
    charter_path: Path, *, run_id: str, repo_dir: Path, srs_path: Path
) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    slug = _slug_from_run_id(run_id)
    srs_sha = _compute_sha256(srs_path)
    try:
        relative_srs = os.path.relpath(srs_path, repo_dir)
    except ValueError:
        relative_srs = str(srs_path)
    payload: Dict[str, object] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "slug": slug,
        "created_at": now,
        "repo_dir": str(repo_dir),
        "srs": {"path": relative_srs, "sha256": srs_sha},
        "models": {
            "maestro": {"name": "maestro"},
            "apprentice": {"name": "apprentice"},
        },
        "allowed_tools": {
            "tier1": ["run_command", "format_runner", "lint_runner"],
            "tier2": [],
            "git": ["git_status"],
        },
        "quality_gates": {
            "default": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}}
            ],
            "on_merge_candidate": [
                {"name": "format", "tool": "format_runner", "args": {"mode": "check"}}
            ],
        },
        "branch_strategy": {
            "type": "feature_branch",
            "name_template": "agent/{run_id}/{slug}",
            "base_branch": "main",
        },
        "stop_conditions": {"max_cycles": 10, "max_failures": 2, "max_minutes": 60},
        "policies": {
            "require_approval_for": [],
            "prohibit_outside_workspace": True,
            "prefer_revert_over_reset": True,
            "secrets_handling": "redact",
        },
    }
    charter_path.write_text(json.dumps(payload, indent=2))


def _load_or_create_charter(repo_dir: Path, run_id: str, srs_path: Path) -> Path:
    agent_root = repo_dir / ".agentmaestro"
    agent_root.mkdir(parents=True, exist_ok=True)
    charter_path = agent_root / "run_charter.json"
    if not charter_path.exists():
        _write_charter(charter_path, run_id=run_id, repo_dir=repo_dir, srs_path=srs_path)
    else:
        data = json.loads(charter_path.read_text())
        validate_run_charter(data)
    return charter_path


def _summary_path(repo_dir: Path, run_id: str) -> Path:
    run_root = repo_dir / ".agentmaestro" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root / "summary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal orchestrator runner")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--srs", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    repo_dir.mkdir(parents=True, exist_ok=True)
    srs_path = Path(args.srs)
    if not srs_path.is_absolute():
        srs_path = (repo_dir / srs_path).resolve()
    if not srs_path.exists():
        raise FileNotFoundError(f"srs file not found: {srs_path}")

    charter_path = _load_or_create_charter(repo_dir, args.run_id, srs_path)
    summary_path = _summary_path(repo_dir, args.run_id)

    summary: Dict[str, str]
    exit_code = 0
    try:
        if args.dry_run:
            summary = {"status": "dry_run", "reason": "dry-run requested"}
        else:
            summary = orchestrate(str(repo_dir), str(charter_path))
    except Exception as exc:  # pragma: no cover - bubble up errors after writing summary
        exit_code = 1
        summary = {"status": "error", "reason": str(exc)}
    finally:
        summary["run_id"] = args.run_id
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        print(json.dumps(summary))
        if exit_code:
            raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
