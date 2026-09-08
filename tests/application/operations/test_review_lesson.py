"""Reviewer-backed judge producers fill the existing *Review schemas."""

from __future__ import annotations

import json

from lesson_builder.application.operations.review_lesson import K_PEDAGOGY_SCORE_AXES
from lesson_builder.application.operations.review_lesson import answer_review
from lesson_builder.application.operations.review_lesson import build_answer_prompt
from lesson_builder.application.operations.review_lesson import build_attempt_prompt
from lesson_builder.application.operations.review_lesson import build_open_rubric_prompt
from lesson_builder.application.operations.review_lesson import default_judge
from lesson_builder.application.operations.review_lesson import median_pedagogy_review
from lesson_builder.application.operations.review_lesson import naturalness_review
from lesson_builder.application.operations.review_lesson import noop_judge
from lesson_builder.application.operations.review_lesson import objective_alignment_review
from lesson_builder.application.operations.review_lesson import pedagogy_review
from lesson_builder.application.operations.review_lesson import reviewer_sample_count
from lesson_builder.application.operations.review_lesson import run_pedagogy_surface
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview
from lesson_builder.domain.lesson.validation.review_payloads import project_answer_review


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


def test_answer_review_given_opaque_choose_answer_expect_authored_answer_restored():
    opaque_id = project_answer_review(_closed_prompt_lesson()).questions[0]["options"][0]["option_id"]
    out = answer_review(
        lesson=_closed_prompt_lesson(),
        agent=_CannedAgent(payload={"answers": [{"id": "choose-one", "answer": opaque_id}]}),
    )

    assert out == {"answers": [{"id": "choose-one", "answer": "a"}]}


def test_answer_prompt_given_closed_payload_expect_full_keyless_learner_view():
    lesson = _closed_prompt_lesson()

    payload = json.loads(build_answer_prompt(lesson).split("EXERCISES:\n", 1)[1])
    _assert_keyless_projection(payload)


def test_answer_prompt_given_answer_signaling_option_ids_expect_opaque_reviewer_input():
    lesson = _closed_prompt_lesson()
    lesson["elements"][0]["payload"]["options"] = [
        {"option_id": "correct-answer", "text": "ja"},
        {"option_id": "incorrect-answer", "text": "nei"},
    ]

    prompt = build_answer_prompt(lesson)

    assert "correct-answer" not in prompt
    assert "incorrect-answer" not in prompt
    assert "review-option-" in prompt


def test_answer_prompt_given_referenced_section_expect_context_without_answer_fields():
    lesson = {
        "elements": [
            {
                "element_kind": "section",
                "id": "sec-context",
                "role": "model",
                "title": "A useful exchange",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [{"kind": "text", "value": "Kan du hjelpe meg?"}],
                        "translation": "Can you help me?",
                        "speaker_id": "per",
                        "speaker_name": "Per",
                        "dialogue_id": "help-1",
                    }
                ],
            },
            {
                "element_kind": "exercise",
                "id": "choose-context",
                "operation": "choose",
                "prompt": [],
                "derived_from": [{"section_id": "sec-context"}],
                "payload": {
                    "stem": [],
                    "options": [
                        {"option_id": "a", "text": "Ja"},
                        {"option_id": "b", "text": "Nei"},
                    ],
                    "answer_id": "a",
                },
            },
        ]
    }

    payload = json.loads(build_answer_prompt(lesson).split("EXERCISES:\n", 1)[1])
    question = payload[0]
    assert question["lesson_context"][0]["blocks"][0]["no"] == "Kan du hjelpe meg?"
    assert "answer_id" not in question
    assert all("correct" not in option for option in question["options"])


def test_attempt_prompt_given_hidden_open_fields_expect_true_attempt_surface():
    lesson = {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "speak-one",
                "operation": "speak",
                "prompt": [{"kind": "text", "value": "Say the exact reply."}],
                "payload": {"target": "Jeg kommer.", "feedback": "hidden"},
            },
            {
                "element_kind": "exercise",
                "id": "write-one",
                "operation": "write",
                "prompt": [{"kind": "text", "value": "Write a reply."}],
                "payload": {
                    "response_language": "no",
                    "criteria": [{"id": "greeting", "instruction": "Use a greeting."}],
                    "judge_prompt": "hidden rubric",
                },
            },
        ]
    }

    prompt = build_attempt_prompt(lesson)

    assert "Jeg kommer." not in prompt
    assert "hidden rubric" not in prompt
    assert '"response_language": "no"' in prompt
    assert "derived_from" not in prompt.split("EXERCISES:", 1)[1]


def test_attempt_prompt_given_closed_task_expect_whole_situation_meaning_required():
    prompt = build_attempt_prompt(_LESSON)

    assert "whole stated situation and requested meaning" in prompt
    assert "wrong object, person, event, or context" in prompt
    assert "no visible answer fits the complete situation" in prompt


def test_open_rubric_prompt_given_write_task_expect_hidden_rubric_data_included():
    prompt = build_open_rubric_prompt(
        {
            "elements": [
                {
                    "element_kind": "exercise",
                    "id": "write-one",
                    "operation": "write",
                    "prompt": [{"kind": "text", "value": "Write a Norwegian reply."}],
                    "payload": {
                        "response_language": "no",
                        "criteria": [{"id": "meaning", "instruction": "Preserves the meaning."}],
                        "judge_prompt": "Check each criterion.",
                    },
                }
            ]
        }
    )

    assert "Write a Norwegian reply." in prompt
    assert "Preserves the meaning." in prompt
    assert "Check each criterion." in prompt


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


def test_default_judge_given_schema_aware_agent_expect_four_advisory_reviews():
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


def test_noop_judge_given_any_lesson_expect_all_reviews_none():
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
    assert reviewer_sample_count() == 1


def test_reviewer_sample_count_given_env_overrides_expect_parsed(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "3")
    assert reviewer_sample_count() == 3


def test_reviewer_sample_count_given_zero_expect_one(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "0")
    assert reviewer_sample_count() == 1


def test_reviewer_sample_count_given_non_int_expect_one(monkeypatch):
    monkeypatch.setenv("NORSK_REVIEWER_SAMPLES", "abc")
    assert reviewer_sample_count() == 1


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
    review, status = run_pedagogy_surface(_LESSON, agent)

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
    review, status = run_pedagogy_surface(_LESSON, agent)

    # assert: exactly 1 call, single review returned unchanged
    assert agent.invoke_count == 1
    assert review is not None
    assert review["scores"]["on_concept"] == 3
    assert status is None


def _closed_prompt_lesson() -> dict:
    """Build closed exercises covering every answer-projection shape."""
    return {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "choose-one",
                "operation": "choose",
                "prompt": [],
                "payload": {
                    "stem": [],
                    "options": [
                        {"option_id": "a", "text": "ja"},
                        {"option_id": "b", "text": "nei"},
                    ],
                    "answer_id": "a",
                },
            },
            {
                "element_kind": "exercise",
                "id": "recall-one",
                "operation": "recall_fill",
                "prompt": [],
                "payload": {
                    "segments": [
                        {"kind": "span", "spans": [{"kind": "text", "value": "Jeg "}]},
                        {
                            "kind": "blank",
                            "blank_id": "verb",
                            "options": ["er", "var"],
                            "answer_index": 0,
                        },
                    ]
                },
            },
            {
                "element_kind": "exercise",
                "id": "categorize-one",
                "operation": "categorize",
                "prompt": [],
                "payload": {
                    "buckets": [
                        {"bucket_id": "yes", "label": "Positive"},
                        {"bucket_id": "no", "label": "Negative"},
                    ],
                    "items": [{"item_id": "i1", "text": "ja", "bucket_id": "yes"}],
                },
            },
            {
                "element_kind": "exercise",
                "id": "judge-one",
                "operation": "judge",
                "prompt": [],
                "payload": {
                    "sentence": [{"kind": "text", "value": "Det er fint."}],
                    "is_correct": True,
                    "feedback": "hidden",
                },
            },
            {
                "element_kind": "exercise",
                "id": "build-one",
                "operation": "build",
                "prompt": [],
                "payload": {
                    "tokens": [
                        {"token_id": "t1", "text": "Jeg", "fixed": False},
                        {"token_id": "t2", "text": "går", "fixed": True},
                    ],
                    "answer_order": ["t1", "t2"],
                },
            },
            {
                "element_kind": "exercise",
                "id": "find-one",
                "operation": "find_fix",
                "prompt": [],
                "payload": {
                    "tokens": [{"token_id": "t1", "text": "går"}],
                    "error_token_id": "t1",
                    "feedback": "hidden",
                },
            },
            {
                "element_kind": "exercise",
                "id": "match-one",
                "operation": "match_pairs",
                "prompt": [],
                "payload": {
                    "left": [{"left_id": "l1", "text": "ja"}],
                    "right": [{"right_id": "r1", "text": "positive"}],
                    "pairs": [{"left_id": "l1", "right_id": "r1"}],
                },
            },
        ]
    }


def _assert_keyless_projection(payload: list[dict]) -> None:
    """Assert that answer projection strips every hidden answer field."""
    by_id = {item["id"]: item for item in payload}
    choose = by_id["choose-one"]
    recall = by_id["recall-one"]
    categorize = by_id["categorize-one"]
    judge = by_id["judge-one"]
    build = by_id["build-one"]
    find_fix = by_id["find-one"]
    match = by_id["match-one"]
    assert [option["text"] for option in choose["options"]] == ["ja", "nei"]
    assert all("correct" not in option for option in choose["options"])
    assert "answer_id" not in choose
    assert "answer_index" not in recall["blanks"][0]
    assert "bucket_id" not in categorize["items"][0]
    assert "is_correct" not in judge and "feedback" not in judge
    assert "answer_order" not in build
    assert "error_token_id" not in find_fix and "feedback" not in find_fix
    assert "pairs" not in match
