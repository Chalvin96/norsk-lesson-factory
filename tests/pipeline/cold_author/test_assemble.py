"""Task E Step 4: ``assemble_lesson`` builds a gate-clean ``Lesson`` from the
cold-author stage outputs, injecting deterministic defaults, the review pool,
anchor coverage, and a full-gate preflight (zero blockers).

Entry point: ``assemble_lesson``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results
from lesson_builder.pipeline.cold_author.assemble import assemble_lesson
from lesson_builder.pipeline.cold_author.models import AssemblyError
from lesson_builder.schema import Lesson


def _metadata() -> dict[str, Any]:
    return {
        "title": "Adjective agreement",
        "goal": "Learn indefinite adjective agreement.",
        "cefr_level": "A1",
        "objectives": [
            {"id": "obj_001", "statement": "Recognize forms.", "bloom_targets": ["understand"]},
        ],
    }


def _sections() -> list[dict[str, Any]]:
    return [
        {
            "element_kind": "section",
            "id": "sec_orient",
            "role": "orient",
            "objective_ids": [],
            "title": "Overview",
            "blocks": [
                {"kind": "paragraph", "spans": [{"kind": "text", "value": "Intro."}]}
            ],
        },
        {
            "element_kind": "section",
            "id": "sec_model",
            "role": "model",
            "objective_ids": ["obj_001"],
            "title": "Forms",
            "blocks": [
                {"kind": "paragraph", "spans": [{"kind": "text", "value": "et fint hus"}]}
            ],
        },
    ]


def _exercises() -> list[dict[str, Any]]:
    return [
        {
            "element_kind": "exercise",
            "id": "ex_judge",
            "operation": "judge",
            "objective_id": "obj_001",
            "bloom_level": "understand",
            "derived_from": [],
            "prompt": [{"kind": "text", "value": "Riktig?"}],
            "explanation": [{"kind": "text", "value": "Ja."}],
            "payload": {
                "sentence": [{"kind": "text", "value": "en fin bil"}],
                "is_correct": True,
                "feedback": "Korrekt.",
            },
        },
        {
            "element_kind": "exercise",
            "id": "ex_choose",
            "operation": "choose",
            "objective_id": "obj_001",
            "bloom_level": "understand",
            "derived_from": [],
            "prompt": [{"kind": "text", "value": "Velg."}],
            "explanation": None,
            "payload": {
                "options": [
                    {"option_id": "o0", "text": "fine biler"},
                    {"option_id": "o1", "text": "fin biler"},
                ],
                "answer_id": "o0",
            },
        },
    ]


def _requirements(missing_anchor: str | None = None) -> dict[str, Any]:
    anchors = ["en fin bil", "fine biler"]
    if missing_anchor:
        anchors.append(missing_anchor)
    return {
        "slug": "adjective_agreement",
        "required_anchor_forms": anchors,
        "notes": "teach agreement",
    }


def test_assemble_lesson_given_clean_inputs_expect_gate_clean_lesson():
    result = assemble_lesson(
        "adjective_agreement",
        _requirements(),
        _metadata(),
        _sections(),
        _exercises(),
    )
    Lesson.model_validate(result)
    assert result["concept_slug"] == "adjective_agreement"
    assert result["grounding_mode"] == "fallback_no_wiki"
    assert result["title"] == "Adjective agreement"
    assert result["cefr_level"] == "A1"
    # review pool: one pool per objective, cards reference exercises.
    pools = result["review_pool"]["pools"]
    assert len(pools) == 1
    assert pools[0]["objective_id"] == "obj_001"
    assert pools[0]["key"] == "obj_001"
    assert {c["exercise_id"] for c in pools[0]["cards"]} == {"ex_judge", "ex_choose"}


def test_assemble_lesson_given_clean_inputs_expect_zero_blocking_gate_results():
    result = assemble_lesson(
        "adjective_agreement",
        _requirements(),
        _metadata(),
        _sections(),
        _exercises(),
    )
    gate_results = gate_lesson_results(
        result,
        requirements=_requirements(),
        baseline_export=None,
        recorded_requirements_hash=None,
    )
    blocking = [r for r in gate_results if r.is_blocking]
    assert blocking == [], (
        "expected zero blockers from the full preflight; got: "
        + json.dumps([r.message for r in blocking], ensure_ascii=False)
    )


def test_assemble_lesson_given_missing_anchor_expect_injected_into_recap_section():
    # setup: "bilen er fin" is required but does NOT appear in sections/exercises.
    result = assemble_lesson(
        "adjective_agreement",
        _requirements(missing_anchor="bilen er fin"),
        _metadata(),
        _sections(),
        _exercises(),
    )
    all_text = json.dumps(result["elements"], ensure_ascii=False).lower()
    assert "bilen er fin" in all_text
    # the injected anchor appears in a recap/orient section (not as an exercise).
    recap_sections = [
        el for el in result["elements"]
        if el.get("element_kind") == "section" and el.get("role") in ("recap", "orient")
    ]
    assert any("bilen er fin" in json.dumps(s, ensure_ascii=False).lower() for s in recap_sections)


def test_assemble_lesson_given_uncovered_objective_expect_raises():
    # setup: objective obj_002 declared but no section/exercise covers it.
    metadata = {
        "title": "T",
        "goal": "G.",
        "cefr_level": "A1",
        "objectives": [
            {"id": "obj_001", "statement": "S.", "bloom_targets": ["understand"]},
            {"id": "obj_002", "statement": "S2.", "bloom_targets": ["apply"]},
        ],
    }
    with pytest.raises(AssemblyError) as exc_info:
        assemble_lesson(
            "slug",
            _requirements(),
            metadata,
            _sections(),
            _exercises(),
        )
    assert "obj_002" in str(exc_info.value)
