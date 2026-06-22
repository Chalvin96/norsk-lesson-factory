"""Task E Step 2: ``author_sections`` synthesizes teaching ``Section`` elements
from the metadata objectives + requirements, honoring the real schema literal
roles and objective-coverage invariant.

Entry point: ``author_sections``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from lesson_builder.pipeline.cold_author.models import StageFailure, StageOK
from lesson_builder.pipeline.cold_author.sections import author_sections
from lesson_builder.schema import Lesson
from lesson_builder.schema.elements import Section


def _objectives() -> list[dict[str, Any]]:
    return [
        {"id": "obj_001", "statement": "Recognize indefinite forms.", "bloom_targets": ["understand"]},
        {"id": "obj_002", "statement": "Produce agreement.", "bloom_targets": ["apply"]},
    ]


def _requirements() -> dict[str, Any]:
    return {
        "slug": "adjective_agreement",
        "required_anchor_forms": ["en fin bil"],
        "notes": "teach agreement",
    }


class _FakeAgent:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def invoke(self, prompt: str, **kw: Any) -> str:
        self.calls += 1
        return self._response


_WELL_FORMED = json.dumps(
    {
        "sections": [
            {
                "role": "orient",
                "objective_ids": [],
                "title": "Overview",
                "blocks": [
                    {"kind": "paragraph", "spans": [{"kind": "text", "value": "Intro to agreement."}]}
                ],
            },
            {
                "role": "model",
                "objective_ids": ["obj_001"],
                "title": "Model forms",
                "blocks": [
                    {"kind": "paragraph", "spans": [{"kind": "text", "value": "en fin bil example."}]}
                ],
            },
            {
                "role": "model",
                "objective_ids": ["obj_002"],
                "title": "Model agreement",
                "blocks": [
                    {"kind": "paragraph", "spans": [{"kind": "text", "value": "Producing forms."}]}
                ],
            },
        ]
    },
    ensure_ascii=False,
)


def test_author_sections_given_well_formed_response_expect_schema_valid_sections_covering_all_objectives():
    # execute
    result = author_sections(
        _objectives(),
        _requirements(),
        author_agent=_FakeAgent(_WELL_FORMED),
    )

    # assert: a StageOK carrying schema-valid sections that cover every objective.
    assert isinstance(result, StageOK)
    sections = result.payload
    assert len(sections) == 3
    for section in sections:
        TypeAdapter(Section).validate_python(section)
    covered = {oid for s in sections for oid in s["objective_ids"]}
    assert covered == {"obj_001", "obj_002"}


def test_author_sections_given_role_teach_expect_rejected():
    # setup: 'teach' is not a valid role literal.
    teach = json.dumps(
        {"sections": [{"role": "teach", "objective_ids": [], "title": "x", "blocks": []}]},
        ensure_ascii=False,
    )

    result = author_sections(_objectives(), _requirements(), author_agent=_FakeAgent(teach))

    assert isinstance(result, StageFailure)
    assert result.stage == "sections"


def test_author_sections_given_non_paragraph_block_expect_rejected():
    # regression (J1 cold-author, 2026-06-20): the LLM emitted a `table` block with a
    # guessed-wrong nested schema. Cold-author sections must be paragraph-only.
    table = json.dumps(
        {
            "sections": [
                {
                    "role": "model",
                    "objective_ids": ["obj_001"],
                    "title": "x",
                    "blocks": [
                        {"kind": "table", "columns": ["a", "b"], "rows": [["1", "2"]]}
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )

    result = author_sections(_objectives(), _requirements(), author_agent=_FakeAgent(table))

    assert isinstance(result, StageFailure)
    assert result.stage == "sections"
    assert "paragraph" in result.reason.lower()


def test_author_sections_given_model_without_exactly_one_objective_expect_failure():
    # setup: model role requires exactly one objective_id.
    bad = json.dumps(
        {
            "sections": [
                {"role": "model", "objective_ids": [], "title": "x", "blocks": []},
            ]
        },
        ensure_ascii=False,
    )

    result = author_sections(_objectives(), _requirements(), author_agent=_FakeAgent(bad))

    assert isinstance(result, StageFailure)


def test_author_sections_given_uncovered_objective_expect_failure():
    # setup: only obj_001 is referenced; obj_002 is uncovered.
    uncovered = json.dumps(
        {
            "sections": [
                {
                    "role": "model",
                    "objective_ids": ["obj_001"],
                    "title": "x",
                    "blocks": [
                        {"kind": "paragraph", "spans": [{"kind": "text", "value": "ok"}]}
                    ],
                },
            ]
        },
        ensure_ascii=False,
    )

    result = author_sections(_objectives(), _requirements(), author_agent=_FakeAgent(uncovered))

    assert isinstance(result, StageFailure)
    assert "obj_002" in result.reason


def test_author_sections_given_sections_assemble_into_lesson_with_objectives():
    # the returned sections must be usable inside a full Lesson assembly.
    result = author_sections(
        _objectives(),
        _requirements(),
        author_agent=_FakeAgent(_WELL_FORMED),
    )
    assert isinstance(result, StageOK)
    sections = result.payload

    lesson = {
        "key": "adjective_agreement",
        "concept_slug": "adjective_agreement",
        "grounding_mode": "fallback_no_wiki",
        "title": "T",
        "cefr_level": "A1",
        "goal": "G.",
        "objectives": _objectives(),
        "elements": sections,
        "review_pool": {"pools": []},
    }
    # Should validate (modulo review_pool coverage which exercises provide).
    Lesson.model_validate(lesson)
