from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, List, Mapping, Sequence

from .srs_builder import SRSSection, SRSBuilder


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ChatMessage:
    id: int
    ts: str
    role: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "role": self.role,
            "content": self.content,
            "meta": self.meta,
        }


class ChatTranscript:
    def __init__(self, run_root: Path):
        self.chat_dir = run_root / "chat"
        self.transcript_path = self.chat_dir / "transcript.jsonl"
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        self._last_id = self._read_last_id()

    def _read_last_id(self) -> int:
        if not self.transcript_path.exists():
            return 0
        try:
            with self.transcript_path.open("r", encoding="utf-8") as handle:
                last = 0
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last = max(last, int(item.get("id", 0)))
                return last
        except OSError:
            return 0

    def append(self, role: str, content: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            self._last_id += 1
            message = ChatMessage(
                id=self._last_id,
                ts=_now_iso(),
                role=role,
                content=content,
                meta=meta or {},
            )
            line = json.dumps(message.to_dict(), ensure_ascii=False)
            with self.transcript_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return message.to_dict()

    def read_since(self, since: int = 0) -> tuple[list[dict[str, Any]], int]:
        messages: list[dict[str, Any]] = []
        if not self.transcript_path.exists():
            return [], since
        with self.transcript_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("id", 0) > since:
                    messages.append(payload)
        next_since = messages[-1]["id"] if messages else since
        return messages, next_since

    def reset(self) -> None:
        with self.lock:
            if self.transcript_path.exists():
                self.transcript_path.unlink()
            self._last_id = 0


class MaestroChatEngine:
    SECTION_FLOW: List[str] = [
        "project_summary",
        "goals_non_goals",
        "functional_requirements",
        "acceptance_criteria",
        "risks_assumptions",
    ]

    QUESTION_PROMPTS: dict[str, str] = {
        "project_summary": "Summarize the initiative briefly: what is being built and why?",
        "goals_non_goals": "List a few primary goals and explicitly call out a non-goal.",
        "functional_requirements": "What behaviors or commands does the system need to perform?",
        "acceptance_criteria": "What measurable conditions indicate completion?",
        "risks_assumptions": "What assumptions or risks should we capture for this work?",
    }

    def respond(self, user_message: str, builder: SRSBuilder) -> dict[str, Any]:
        normalized = user_message.strip()
        locked_sections = set(builder.locked_sections)
        next_section_id = next(
            (section_id for section_id in self.SECTION_FLOW if section_id not in locked_sections),
            None,
        )
        meta: dict[str, Any] = {"questions": [], "srs_updates": [], "requires_user_decision": False}
        if not next_section_id:
            meta["questions"].append("All core sections look good—would you like to review the plan now?")
            return {
                "content": "Everything looks locked. Let me know if you want to tweak anything else.",
                "meta": meta,
            }

        try:
            section = builder._get_section(next_section_id)
        except KeyError:
            meta["questions"].append("Tell me what section you'd like to lock next.")
            return {"content": "I couldn't find that section—can you rephrase?", "meta": meta}

        question = self.QUESTION_PROMPTS.get(next_section_id, f"Tell me about {section.title}.")
        meta["questions"].append(question)
        action = "lock" if "lock" in normalized.lower() else "draft"
        content = normalized or f"Draft content for {section.title}."
        meta["srs_updates"].append(
            {"section_id": section.section_id, "action": action, "content": content}
        )
        if action == "lock":
            meta["requires_user_decision"] = True

        summary = f"I recorded a {action} for {section.title}."
        if action == "lock":
            summary += " It's now locked."
        return {"content": summary, "meta": meta}
