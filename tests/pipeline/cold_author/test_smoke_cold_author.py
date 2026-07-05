"""Task E Step 7: one ``LLM_SMOKE``-gated live test per cold-author stage, so no
stub ships without a flip-to-live test. Run with ``LLM_SMOKE=1``.

Entry point: each stage + the assembled flow against the real author agent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results
from lesson_builder.pipeline.cold_author.assemble import assemble_lesson
from lesson_builder.pipeline.cold_author.exercises import author_exercises
from lesson_builder.pipeline.cold_author.metadata_objectives import author_metadata_objectives
from lesson_builder.pipeline.cold_author.models import StageOK
from lesson_builder.pipeline.cold_author.sections import author_sections
from lesson_builder.pipeline.concept_requirements import ConceptRequirements
from lesson_builder.schema import Lesson

ROOT = Path(__file__).resolve().parents[3]


def _requirements() -> ConceptRequirements:
    return ConceptRequirements.model_validate(
        json.loads(
            (ROOT / "data" / "concept_requirements" / "adjective_agreement.json").read_text(
                encoding="utf-8"
            )
        )
    )


pytestmark = pytest.mark.skipif(
    not os.environ.get("LLM_SMOKE"), reason="set LLM_SMOKE=1 to run the live cold-author stages"
)


def test_smoke_author_metadata_objectives_given_real_author_expect_stage_ok():
    result = author_metadata_objectives(
        "adjective_agreement", _requirements(), author_agent=author()
    )
    assert isinstance(result, StageOK)
    metadata = result.payload.model_dump(mode="json")
    requirements = _requirements().model_dump(mode="json")
    assert metadata["cefr_level"] == requirements["cefr_level"]
    assert metadata["objectives"] == requirements["objectives"]


def test_smoke_author_sections_given_real_author_expect_stage_ok():
    metadata = author_metadata_objectives(
        "adjective_agreement", _requirements(), author_agent=author()
    )
    assert isinstance(metadata, StageOK)
    objectives = metadata.payload.model_dump(mode="json")["objectives"]
    result = author_sections(objectives, _requirements().model_dump(mode="json"), author_agent=author())
    assert isinstance(result, StageOK)
    assert len(result.payload) >= 1


def test_smoke_author_exercises_given_real_author_expect_stage_ok():
    metadata = author_metadata_objectives(
        "adjective_agreement", _requirements(), author_agent=author()
    )
    assert isinstance(metadata, StageOK)
    objectives = metadata.payload.model_dump(mode="json")["objectives"]
    result = author_exercises(objectives, author_agent=author())
    assert isinstance(result, StageOK)
    assert len(result.payload) >= 2


def test_smoke_assemble_and_gate_given_real_stages_expect_zero_blockers():
    requirements = _requirements()
    agent = author()
    metadata = author_metadata_objectives(
        "adjective_agreement", requirements, author_agent=agent
    )
    assert isinstance(metadata, StageOK)
    metadata_dict = metadata.payload.model_dump(mode="json")
    sections = author_sections(
        metadata_dict["objectives"], requirements.model_dump(mode="json"), author_agent=agent
    )
    assert isinstance(sections, StageOK)
    exercises = author_exercises(metadata_dict["objectives"], author_agent=agent)
    assert isinstance(exercises, StageOK)

    draft = assemble_lesson(
        "adjective_agreement", requirements, metadata_dict, sections.payload, exercises.payload
    )
    Lesson.model_validate(draft)
    gate = gate_lesson_results(
        draft,
        requirements=requirements.model_dump(mode="json"),
        baseline_export=None,
        recorded_requirements_hash=None,
    )
    blockers = [r for r in gate if r.is_blocking]
    assert blockers == [], "cold-authored draft must have zero blockers before entering QA"
