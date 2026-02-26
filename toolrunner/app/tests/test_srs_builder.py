from pathlib import Path

import json
import pytest

from toolrunner.app.srs_builder import SRSBuilder, SRSSection


@pytest.fixture
def minimal_sections() -> list[SRSSection]:
    return [
        SRSSection(
            section_id="summary",
            title="Project Summary",
            meaning="Describe the initiative.",
            template="One paragraph summary.",
            checklist=["Clearly describe purpose."],
            example="Summarize the project succinctly.",
        )
    ]


def test_prompts_show_template(minimal_sections, tmp_path: Path):
    builder = SRSBuilder(tmp_path, sections=minimal_sections)
    section = builder.current_section()
    prompt = builder.prompt(section.section_id)
    assert prompt["title"] == "Project Summary"
    assert prompt["meaning"].startswith("Describe the initiative")
    assert prompt["locked"] is False


def test_record_section_writes_files(minimal_sections, tmp_path: Path):
    builder = SRSBuilder(tmp_path, sections=minimal_sections)
    section = builder.current_section()
    content = "This project builds a testable SRS."
    locked = builder.record_section(section.section_id, content)
    assert builder.is_locked(section.section_id)
    builder.save()
    srs_text = Path(tmp_path / "SRS.md").read_text()
    assert "Project Summary" in srs_text
    assert "testable SRS" in srs_text
    lock_data = json.loads((tmp_path / "SRS.lock.json").read_text())
    assert lock_data["locked_sections"][section.section_id]["sha256"] == locked["sha256"]


def test_empty_content_rejected(minimal_sections, tmp_path: Path):
    builder = SRSBuilder(tmp_path, sections=minimal_sections)
    with pytest.raises(ValueError):
        builder.record_section("summary", "   ")


def test_pending_sections_reflects_progress(minimal_sections, tmp_path: Path):
    builder = SRSBuilder(tmp_path, sections=minimal_sections)
    assert builder.pending_sections()
    builder.record_section("summary", "done")
    assert builder.pending_sections() == []
