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
    K_PEDAGOGY_SCORE_AXES,
    _load_active_rules_table,
    _reviewer_sample_count,
    _run_pedagogy_surface,
    answer_review,
    default_judge,
    median_pedagogy_review,
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


# ---------------------------------------------------------------------------
# Median-of-N reviewer score aggregation (WI 19)
# ---------------------------------------------------------------------------

def _pedagogy_review_dict(on_concept: int = 4, *, summary: str = "solid") -> dict:
    return {
        "scores": dict.fromkeys(K_PEDAGOGY_SCORE_AXES, 4) | {"on_concept": on_concept},
        "summary": summary,
        "passes": ["on_concept"],
        "issues": [],
    }


def test_median_pedagogy_review_given_empty_list_expect_none():
    assert median_pedagogy_review([]) is None


def test_median_pedagogy_review_given_single_review_expect_unchanged():
    review = _pedagogy_review_dict(on_concept=3, summary="only")
    result = median_pedagogy_review([review])
    assert result is review


def test_median_pedagogy_review_given_three_reviews_expect_per_axis_median():
    # setup: on_concept varies [3, 5, 4], every other axis constant at 4
    reviews = [
        _pedagogy_review_dict(on_concept=3, summary="first"),
        _pedagogy_review_dict(on_concept=5, summary="second"),
        _pedagogy_review_dict(on_concept=4, summary="third"),
    ]

    # execute
    merged = median_pedagogy_review(reviews)

    # assert: median_low of [3, 5, 4] is 4; summary/passes/issues from first sample
    assert merged is not None
    assert merged["scores"]["on_concept"] == 4
    for axis in K_PEDAGOGY_SCORE_AXES:
        if axis != "on_concept":
            assert merged["scores"][axis] == 4
    assert merged["summary"] == "first"
    assert merged["passes"] == ["on_concept"]
    assert merged["issues"] == []


def test_reviewer_sample_count_given_no_env_expect_default_one(monkeypatch):
    monkeypatch.delenv("NORSK_REVIEWER_SAMPLES", raising=False)
    assert _reviewer_sample_count() == 1


def test_reviewer_sample_count_given_env_overrides_expect_parsed(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "3")
    assert _reviewer_sample_count() == 3


def test_reviewer_sample_count_given_zero_expect_one(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "0")
    assert _reviewer_sample_count() == 1


def test_reviewer_sample_count_given_non_int_expect_one(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "abc")
    assert _reviewer_sample_count() == 1


class _CyclePedagogyAgent:
    """Returns pedagogy payloads cycling through on_concept values [3, 5, 4].

    Counts invocations so tests can assert the reviewer was called N times.
    Implements .structured(schema) -> object with .invoke(prompt) -> result
    exposing .model_dump(mode="json").
    """

    def __init__(self, on_concept_cycle: list[int]):
        self._cycle = list(on_concept_cycle)
        self._idx = 0
        self.invoke_count = 0
        self._schema = None

    def structured(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt, **kw):
        on_concept = self._cycle[self._idx % len(self._cycle)]
        self._idx += 1
        self.invoke_count += 1
        payload = _pedagogy_review_dict(on_concept=on_concept, summary=f"sample-{self.invoke_count}")
        return self._schema.model_validate(payload)


def test_run_pedagogy_surface_given_n3_env_expect_three_invocations(monkeypatch):
    # setup: N=3 via env, fake agent cycles on_concept [3, 5, 4] -> median 4
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "3")
    agent = _CyclePedagogyAgent(on_concept_cycle=[3, 5, 4])

    # execute
    review, status = _run_pedagogy_surface(_LESSON, agent)

    # assert: 3 calls, merged review returned, no failure status
    assert agent.invoke_count == 3
    assert review is not None
    assert review["scores"]["on_concept"] == 4
    assert status is None


def test_run_pedagogy_surface_given_n1_default_expect_single_invocation(monkeypatch):
    # setup: env unset -> N=1 (no behavior change)
    monkeypatch.delenv("NORSK_REVIEWER_SAMPLES", raising=False)
    agent = _CyclePedagogyAgent(on_concept_cycle=[3, 5, 4])

    # execute
    review, status = _run_pedagogy_surface(_LESSON, agent)

    # assert: exactly 1 call, single review returned unchanged
    assert agent.invoke_count == 1
    assert review is not None
    assert review["scores"]["on_concept"] == 3
    assert status is None
