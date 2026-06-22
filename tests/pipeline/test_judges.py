"""Reviewer-backed judge producers fill the existing *Review schemas."""
from __future__ import annotations

import os

import pytest

from lesson_builder.pipeline.checks.validators.naturalness import NaturalnessReview
from lesson_builder.pipeline.checks.validators.objective_alignment import (
    ObjectiveAlignmentReview,
)
from lesson_builder.pipeline.checks.validators.pedagogy import PedagogyReview
from lesson_builder.pipeline.judges import (
    _load_active_rules_table,
    answer_review,
    default_judge,
    naturalness_review,
    noop_judge,
    objective_alignment_review,
    pedagogy_review,
)
from lesson_builder.pipeline.llm.exceptions import BackendDownException


class _CannedAgent:
    """Stands in for reviewer(): .structured(S).invoke(p) -> S or raises."""

    def __init__(self, payload=None, exc=None):
        self._payload, self._exc = payload, exc
        self._schema = None

    def structured(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt, **kw):
        if self._exc:
            raise self._exc
        return self._schema.model_validate(self._payload)


_GOOD_PED = {
    "scores": dict.fromkeys(
        ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"), 4
    ),
    "summary": "solid",
    "passes": ["on_concept"],
    "issues": [],
}
_LESSON = {
    "concept_slug": "ordinal_numbers",
    "objectives": [{"id": "o1"}],
    "elements": [
        {
            "element_kind": "exercise",
            "id": "e1",
            "objective_id": "o1",
            "operation": "judge",
            "prompt": [{"kind": "text", "value": "Mark it"}],
            "payload": {
                "sentence": [{"kind": "text", "value": "x"}],
                "is_correct": True,
                "feedback": "ok",
            },
        }
    ],
}


def test_pedagogy_review_given_valid_agent_response_expect_payload():
    out = pedagogy_review(lesson=_LESSON, agent=_CannedAgent(payload=_GOOD_PED))
    assert PedagogyReview.model_validate(out)  # validates


def test_pedagogy_review_given_backend_down_expect_none():
    out = pedagogy_review(lesson=_LESSON, agent=_CannedAgent(exc=BackendDownException("x")))
    assert out is None


def test_objective_alignment_review_given_valid_agent_response_expect_payload():
    good_alignment = {"passed": True, "summary": "ok", "issues": []}
    out = objective_alignment_review(lesson=_LESSON, agent=_CannedAgent(payload=good_alignment))
    assert ObjectiveAlignmentReview.model_validate(out)


def test_answer_review_given_valid_agent_response_expect_payload():
    good_answers = {"answers": [{"id": "e1", "answer": True}]}
    out = answer_review(lesson=_LESSON, agent=_CannedAgent(payload=good_answers))
    assert out == good_answers


class _SchemaAwareAgent:
    """Returns a payload appropriate to whichever schema is requested."""

    def __init__(self, payload_by_schema_name: dict[str, dict]):
        self._payload_by_schema_name = payload_by_schema_name
        self._schema = None

    def structured(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt, **kw):
        payload = self._payload_by_schema_name[self._schema.__name__]
        return self._schema.model_validate(payload)


def test_default_judge_returns_four_keys():
    agent = _SchemaAwareAgent(
        {
            "PedagogyReview": _GOOD_PED,
            "ObjectiveAlignmentReview": {"passed": True, "summary": "ok", "issues": []},
            "AnswerReview": {"answers": [{"id": "e1", "answer": True}]},
            "NaturalnessReview": {
                "scores": {
                    "idiomatic_phrasing": 4,
                    "register_appropriateness": 5,
                    "terminology_consistency": 4,
                },
                "issues": [],
            },
        }
    )
    out = default_judge(_LESSON, agent=agent)
    assert set(out) == {
        "pedagogy_review",
        "objective_alignment_review",
        "answer_review",
        "naturalness_review",
    }
    assert out["pedagogy_review"] is not None
    assert out["objective_alignment_review"] is not None
    assert out["answer_review"] is not None
    assert out["naturalness_review"] is not None


def test_noop_judge_returns_all_none():
    out = noop_judge(_LESSON)
    assert out == {
        "pedagogy_review": None,
        "objective_alignment_review": None,
        "answer_review": None,
        "naturalness_review": None,
    }


def test_naturalness_review_given_valid_agent_response_expect_payload():
    good_naturalness = {
        "scores": {
            "idiomatic_phrasing": 4,
            "register_appropriateness": 5,
            "terminology_consistency": 4,
        },
        "issues": [],
    }
    out = naturalness_review(lesson=_LESSON, agent=_CannedAgent(payload=good_naturalness))
    assert NaturalnessReview.model_validate(out)


def test_naturalness_review_given_backend_down_expect_none():
    out = naturalness_review(lesson=_LESSON, agent=_CannedAgent(exc=BackendDownException("x")))
    assert out is None


def test_load_active_rules_table_given_style_guide_expect_only_pipe_table():
    table = _load_active_rules_table()
    assert "| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |" in table
    assert "```bans" not in table
    assert "BEGIN MACHINE-READABLE BANS" not in table
    assert "## Rationale" not in table


@pytest.mark.skipif(os.environ.get("LLM_SMOKE") != "1", reason="needs live reviewer backend")
def test_pedagogy_review_real_backend_smoke():
    out = pedagogy_review(lesson=_LESSON)
    assert out is None or PedagogyReview.model_validate(out)
