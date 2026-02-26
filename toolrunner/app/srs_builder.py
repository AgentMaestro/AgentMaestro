from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence

from .orchestrator import now_iso


@dataclass(frozen=True)
class SRSSection:
    section_id: str
    title: str
    meaning: str
    template: str
    checklist: List[str]
    example: str


DEFAULT_SRS_SECTIONS: Sequence[SRSSection] = (
    SRSSection(
        section_id="project_summary",
        title="Project Summary",
        meaning="High-level overview of what the project builds and why.",
        template="Summarize the initiative in 2-3 sentences. Include key stakeholders and the main outcome.",
        checklist=[
            "Is the problem space clearly stated?",
            "Is the expected outcome unambiguous?",
            "Are stakeholders or users mentioned?",
        ],
        example="An autonomous delivery agent that reasons about high priority CLI work...",
    ),
    SRSSection(
        section_id="goals_non_goals",
        title="Goals and Non-Goals",
        meaning="Clarify what success looks like and what is explicitly excluded.",
        template="List bullet goals, then a bullet list of non-goals separated by a short rationale.",
        checklist=[
            "Does each goal tie back to measurable change?",
            "Are non-goals framed as intentional exclusions?",
        ],
        example="- Goal: enable Maestro to auto-generate plans...\n- Non-goal: full UI instrumentation.",
    ),
    SRSSection(
        section_id="users_use_cases",
        title="Users and Use Cases",
        meaning="Describe who interacts with the system and how.",
        template="Name each persona, their context, and the main workflows they trigger.",
        checklist=[
            "Is the Maestro persona described separately?",
            "Is there at least one pre- and post-condition per use case?",
        ],
        example="Maestro: reviews SRS forms, approves risky steps. Apprentice: executes plan steps with tool call sequences.",
    ),
    SRSSection(
        section_id="functional_requirements",
        title="Functional Requirements",
        meaning="Describe behaviors the system must exhibit.",
        template="Use numbered requirements (FR1, FR2...). Mention inputs, processing, outputs.",
        checklist=[
            "Are acceptance gates or rollbacks tied to requirements?",
            "Is tool invocation explicit for automation?",
        ],
        example="FR1: Persist SRS sections to SRS.md after each lock using sha256/locked state.",
    ),
    SRSSection(
        section_id="non_functional_requirements",
        title="Non-Functional Requirements",
        meaning="Capture expectations for performance, reliability, security, etc.",
        template="List categories (perf, security, reliability) and the associated targets.",
        checklist=[
            "Are response time or availability metrics specified?",
            "Is logging/auditing required for critical steps?",
        ],
        example="Logging: every plan run writes step reports + diffs with timestamps within 10s.",
    ),
    SRSSection(
        section_id="interfaces",
        title="Interfaces",
        meaning="Describe APIs, CLI commands, or UI views users interact with.",
        template="List each interface, describe purpose, inputs, outputs.",
        checklist=[
            "Does each interface map to an automation tool or plan step?",
            "Are error conditions described?",
        ],
        example="CLI `srs-builder` prompts for each section and writes SRS.md when sections locked.",
    ),
    SRSSection(
        section_id="data_model",
        title="Data Model / Storage",
        meaning="Outline persistent files, schemas, and relationships.",
        template="Document key files and their structure (SRS.md, plan.json, step reports).",
        checklist=[
            "Are schema requirements tied to enforcement logic?",
            "Is lock metadata stored in JSON with sha256 and timestamps?",
        ],
        example="SRS.lock.json contains {section_id, sha256, locked_at}. Step reports use step_reports/<milestone>/<step>.json.",
    ),
    SRSSection(
        section_id="architecture",
        title="Architecture & Components",
        meaning="Describe key modules and how they interact.",
        template="Name components (SRS builder, failure tracker, orchestrator) and show flow.",
        checklist=[
            "Does the architecture highlight maestro vs apprentice roles?",
            "Are dependencies between modules clear?",
        ],
        example="Maestro runs srs_builder, generates plan JSON, orchestrator executes via tool invoker, step_report stored per step.",
    ),
    SRSSection(
        section_id="operational_requirements",
        title="Operational Requirements",
        meaning="Log focus around logging, config, deployment, and monitoring.",
        template="List logging targets, config files, deployment constraints.",
        checklist=[
            "Are config files (charter.json, plan.json) versioned?",
            "Is there a standard for approval recording?",
        ],
        example="Approvals persist to .agentmaestro/runs/<run>/approvals.json with timestamps and decision context.",
    ),
    SRSSection(
        section_id="acceptance_criteria",
        title="Acceptance Criteria / Definition of Done",
        meaning="Define what completion looks like.",
        template="Bullet criteria referencing plan execution, gate success, artifact creation.",
        checklist=[
            "Do we require approval handling for risk tags?",
            "Are artifacts (SRS.md, plan.json, step reports) validated?",
        ],
        example="SRS md present, plan validated by schema, step reports show gate success and clean repo state.",
    ),
    SRSSection(
        section_id="out_of_scope",
        title="Out of Scope / Future Work",
        meaning="Explicitly call out what isn't built now.",
        template="Explain immediate exclusions and potential future revisits.",
        checklist=[
            "Is advanced UI editing out of scope?",
            "Are features lacking automation mentioned?",
        ],
        example="Real-time approval UI deferred; initial MVP uses CLI + files.",
    ),
    SRSSection(
        section_id="risks_assumptions",
        title="Risks & Assumptions",
        meaning="List key uncertainties and underlying assumptions.",
        template="Pair each risk with mitigation, and each assumption with rationale.",
        checklist=[
            "Are permission errors or temp failures referenced?",
            "Are assumptions about git state, tooling, approvals noted?",
        ],
        example="Risk: Windows permission prevents basetemp cleanup, mitigation: fingerprint-based stop + manual delete message.",
    ),
)


class SRSBuilder:
    def __init__(self, workspace: Path, sections: Optional[Sequence[SRSSection]] = None):
        self.workspace = workspace
        self.sections = list(sections or DEFAULT_SRS_SECTIONS)
        self.lock_path = workspace / "SRS.lock.json"
        self.srs_path = workspace / "SRS.md"
        self.locked_sections: dict[str, dict[str, str]] = {}
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._load_lock()

    def _load_lock(self) -> None:
        if self.lock_path.exists():
            payload = json.loads(self.lock_path.read_text())
            self.locked_sections = payload.get("locked_sections", {})
        else:
            self.locked_sections = {}

    @staticmethod
    def _sha256(content: str) -> str:
        normalized = content.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def prompt(self, section_id: str) -> Mapping[str, object]:
        section = self._get_section(section_id)
        return {
            "section_id": section.section_id,
            "title": section.title,
            "meaning": section.meaning,
            "template": section.template,
            "checklist": list(section.checklist),
            "example": section.example,
            "locked": self.is_locked(section_id),
        }

    def _get_section(self, section_id: str) -> SRSSection:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        raise KeyError(f"unknown section {section_id}")

    def current_section(self) -> Optional[SRSSection]:
        for section in self.sections:
            if section.section_id not in self.locked_sections:
                return section
        return None

    def is_locked(self, section_id: str) -> bool:
        return section_id in self.locked_sections

    def get_section(self, section_id: str) -> SRSSection:
        return self._get_section(section_id)

    def record_section(self, section_id: str, content: str) -> dict[str, str]:
        if not content.strip():
            raise ValueError("section content must not be empty")
        section = self._get_section(section_id)
        sha = self._sha256(content)
        self.locked_sections[section.section_id] = {
            "title": section.title,
            "content": content.strip(),
            "sha256": sha,
            "locked_at": now_iso(),
        }
        return self.locked_sections[section_id]

    def render_srs(self) -> str:
        fragments: List[str] = []
        for section in self.sections:
            locked = self.locked_sections.get(section.section_id)
            body = locked["content"] if locked else "_Section locked content pending._"
            fragments.append(f"## {section.title}\n\n{body}\n")
        return "\n".join(fragments).strip() + "\n"

    def save(self) -> None:
        self.srs_path.write_text(self.render_srs(), encoding="utf-8")
        payload = {
            "version": 1,
            "updated_at": now_iso(),
            "locked_sections": self.locked_sections,
        }
        self.lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def locked_order(self) -> List[str]:
        return list(self.locked_sections)

    def pending_sections(self) -> List[SRSSection]:
        return [section for section in self.sections if section.section_id not in self.locked_sections]
