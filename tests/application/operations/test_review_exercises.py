"""Behavior tests for the strict closed-exercise verifier."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lesson_builder.application.operations.review_exercises import AttemptSurfaceReviewError
from lesson_builder.application.operations.review_exercises import ExerciseReviewError
from lesson_builder.application.operations.review_exercises import OpenRubricReviewError
from lesson_builder.application.operations.review_exercises import verify_attempt_surface
from lesson_builder.application.operations.review_exercises import verify_exercise_package
from lesson_builder.application.operations.review_exercises import verify_exercises
from lesson_builder.application.operations.review_exercises import verify_open_rubrics
from lesson_builder.application.operations.review_exercises import verify_open_semantics
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.domain.lesson.validation.review_payloads import project_answer_review
from tests.clients.llm.fakes import FakeLlmClient


def test_verify_exercises_given_matching_blinded_review_expect_pass():
    report = verify_exercises(
        _lesson(),
        reviewer_agent=_reviewer({"answers": [{"id": "choose-one", "answer": "b"}]}),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["matches"] == 1
    assert report["total"] == 1
    assert report["lesson_hash"].startswith("sha256:")


def test_verify_exercises_given_solved_answer_with_semantic_issue_expect_strict_failure():
    opaque_answer = project_answer_review(_lesson()).questions[0]["options"][1]["option_id"]

    with pytest.raises(ExerciseReviewError) as caught:
        verify_exercises(
            _lesson(),
            reviewer_agent=_reviewer(
                {
                    "answers": [
                        {
                            "id": "choose-one",
                            "answer": opaque_answer,
                            "semantic_issues": [
                                {
                                    "category": "distractor_parallelism",
                                    "reason": "The correct option is much more detailed than its distractor.",
                                    "evidence": "ja / nei",
                                }
                            ],
                        }
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["status"] == "needs_human"
    assert caught.value.report["matches"] == 1
    assert caught.value.report["semantic_issues"][0]["id"] == "choose-one"


def test_verify_exercises_given_ambiguous_review_expect_strict_failure():
    with pytest.raises(ExerciseReviewError) as caught:
        verify_exercises(
            _lesson(),
            reviewer_agent=_reviewer(
                {
                    "answers": [
                        {
                            "id": "choose-one",
                            "answer": None,
                            "status": "ambiguous",
                            "reason": "Both choices fit the visible prompt.",
                        }
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["status"] == "needs_human"
    assert caught.value.report["ambiguous"] == ["choose-one"]


def test_verify_exercises_given_reviewer_outage_expect_explicit_unavailable():
    report = verify_exercises(
        _lesson(),
        reviewer_agent=_reviewer(error=BackendDownException("offline")),
        strict=False,
    )

    assert report["status"] == "reviewer_unavailable"
    assert report["total"] == 1


def test_verify_exercises_given_open_only_package_expect_unverified_open_not_pass():
    lesson = {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "write-reply",
                "operation": "write",
                "prompt": [],
                "payload": {},
            },
            {
                "element_kind": "exercise",
                "id": "say-it",
                "operation": "speak",
                "prompt": [],
                "payload": {},
            },
        ]
    }

    report = verify_exercises(lesson, reviewer_agent=_reviewer(), strict=True)

    assert report["status"] == "unverified_open"
    assert report["total"] == 0
    assert report["open_handles"] == ["say-it", "write-reply"]


def test_verify_exercises_given_closed_and_open_package_expect_open_handles_preserved():
    lesson = _lesson()
    lesson["elements"].append(
        {
            "element_kind": "exercise",
            "id": "write-reply",
            "operation": "write",
            "prompt": [],
            "payload": {},
        }
    )

    report = verify_exercises(
        lesson,
        reviewer_agent=_reviewer({"answers": [{"id": "choose-one", "answer": "b"}]}),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["open_handles"] == ["write-reply"]
    assert report["open_tasks_unverified"] is True


def test_verify_attempt_surface_given_clear_standalone_review_expect_pass():
    report = verify_attempt_surface(
        _lesson(),
        reviewer_agent=_reviewer({"checks": [{"id": "choose-one", "status": "clear"}]}),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["total"] == 1


def test_verify_attempt_surface_given_hidden_context_dependency_expect_strict_failure():
    with pytest.raises(AttemptSurfaceReviewError) as caught:
        verify_attempt_surface(
            _lesson(),
            reviewer_agent=_reviewer(
                {
                    "checks": [
                        {
                            "id": "choose-one",
                            "status": "unanswerable",
                            "reason": "The visible cue omits the needed context.",
                        }
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["unanswerable"] == ["choose-one"]


def test_verify_attempt_surface_given_duplicate_reviewer_ids_expect_strict_failure():
    with pytest.raises(AttemptSurfaceReviewError) as caught:
        verify_attempt_surface(
            _lesson(),
            reviewer_agent=_reviewer(
                {
                    "checks": [
                        {"id": "choose-one", "status": "clear"},
                        {"id": "choose-one", "status": "clear"},
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["duplicates"] == ["choose-one"]


def test_verify_exercise_package_given_open_rubric_pass_expect_unverified_open_status() -> None:
    def response(prompt: str, _model: str | None) -> str:
        if prompt.startswith("Review the standalone attempt-time surface"):
            return json.dumps({"checks": [{"id": "write-reply", "status": "clear"}]})
        return json.dumps({"checks": [{"id": "write-reply", "status": "clear"}]})

    report = verify_exercise_package(
        _write_lesson(),
        reviewer_agent=JobRunner(name="reviewer", client=FakeLlmClient.from_fn(response)),
        strict=True,
    )

    assert report["status"] == "unverified_open"
    assert report["answer_review"]["status"] == "unverified_open"
    assert report["open_rubrics"]["status"] == "pass"


def test_verify_open_rubrics_given_bidirectional_pass_expect_clear_report():
    report = verify_open_rubrics(
        _write_lesson(),
        reviewer_agent=_reviewer({"checks": [{"id": "write-reply", "status": "clear"}]}),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["open_write_handles"] == ["write-reply"]


def test_verify_open_rubrics_given_omitted_obligation_expect_exact_failed_handle():
    with pytest.raises(OpenRubricReviewError) as caught:
        verify_open_rubrics(
            _write_lesson(),
            reviewer_agent=_reviewer(
                {
                    "checks": [
                        {
                            "id": "write-reply",
                            "status": "unanswerable",
                            "reason": "The prompt obligation has no criterion.",
                        }
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["unanswerable"] == ["write-reply"]
    assert caught.value.report["open_write_handles"] == ["write-reply"]


def test_verify_open_rubrics_given_duplicate_reviewer_ids_expect_strict_failure():
    with pytest.raises(OpenRubricReviewError) as caught:
        verify_open_rubrics(
            _write_lesson(),
            reviewer_agent=_reviewer(
                {
                    "checks": [
                        {"id": "write-reply", "status": "clear"},
                        {"id": "write-reply", "status": "clear"},
                    ]
                }
            ),
            strict=True,
        )

    assert caught.value.report["duplicates"] == ["write-reply"]


def test_verify_open_rubrics_given_reviewer_outage_expect_fail_closed():
    with pytest.raises(OpenRubricReviewError) as caught:
        verify_open_rubrics(
            _write_lesson(),
            reviewer_agent=_reviewer(error=BackendDownException("offline")),
            strict=True,
        )

    assert caught.value.report["status"] == "reviewer_unavailable"


def test_verify_open_rubrics_given_no_write_tasks_expect_no_reviewer_call():
    report = verify_open_rubrics(_lesson(), reviewer_agent=_reviewer(error=AssertionError("unused")), strict=True)

    assert report == {"status": "not_applicable", "total": 0, "open_write_handles": []}


def test_verify_exercise_package_given_open_semantic_finding_expect_strict_failure():
    lesson = _write_lesson()

    def response(prompt: str, _model: str | None) -> str:
        if prompt.startswith("Solve each closed exercise"):
            return json.dumps({"answers": []})
        if prompt.startswith("Review the standalone attempt-time surface"):
            return json.dumps({"checks": [{"id": "write-reply", "status": "clear"}]})
        if prompt.startswith("Review every open write and speak task"):
            return json.dumps(
                {
                    "checks": [
                        {
                            "id": "write-reply",
                            "semantic_issues": [
                                {
                                    "category": "rehearsal_vs_production",
                                    "reason": "The prompt claims independent production but supplies the exact sentence.",
                                    "evidence": "Say exactly: Jeg kommer i morgen.",
                                }
                            ],
                        }
                    ]
                }
            )
        return json.dumps({"checks": [{"id": "write-reply", "status": "clear"}]})

    with pytest.raises(ExerciseReviewError) as caught:
        verify_exercise_package(
            lesson,
            reviewer_agent=JobRunner(name="reviewer", client=FakeLlmClient.from_fn(response)),
            strict=True,
        )

    assert caught.value.report["status"] == "needs_human"
    assert caught.value.report["open_semantics"]["semantic_issues"][0]["id"] == "write-reply"


def test_verify_open_semantics_given_clean_open_tasks_expect_pass():
    report = verify_open_semantics(
        _write_lesson(),
        reviewer_agent=_reviewer({"checks": [{"id": "write-reply", "semantic_issues": []}]}),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["semantic_issues"] == []


def test_verify_attempt_surface_given_injected_reviewer_expect_pass():
    report = verify_attempt_surface(
        _lesson(),
        reviewer_agent=_reviewer({"checks": [{"id": "choose-one", "status": "clear"}]}),
        strict=True,
    )

    assert report["status"] == "pass"


def test_verify_exercise_package_given_mixed_closed_and_open_pass_expect_unverified_open_with_handles():
    lesson = _lesson()
    lesson["elements"].append(_write_lesson()["elements"][0])

    def response(prompt: str, _model: str | None) -> str:
        if prompt.startswith("Solve each closed exercise"):
            return json.dumps({"answers": [{"id": "choose-one", "answer": "b"}]})
        if prompt.startswith("Review the standalone attempt-time surface"):
            return json.dumps(
                {
                    "checks": [
                        {"id": "choose-one", "status": "clear"},
                        {"id": "write-reply", "status": "clear"},
                    ]
                }
            )
        return json.dumps({"checks": [{"id": "write-reply", "status": "clear"}]})

    report = verify_exercise_package(
        lesson,
        reviewer_agent=JobRunner(name="reviewer", client=FakeLlmClient.from_fn(response)),
        strict=True,
    )

    assert report["status"] == "unverified_open"
    assert report["open_handles"] == ["write-reply"]
    assert report["open_tasks_unverified"] is True
    assert report["answer_review"]["open_handles"] == ["write-reply"]


def test_verify_exercise_package_given_multiple_surface_failures_expect_atomic_handles():
    lesson = _lesson()
    lesson["elements"].append(_write_lesson()["elements"][0])

    def response(prompt: str, _model: str | None) -> str:
        if prompt.startswith("Solve each closed exercise"):
            return json.dumps({"answers": [{"id": "choose-one", "answer": "b"}]})
        if prompt.startswith("Review the standalone attempt-time surface"):
            return json.dumps(
                {
                    "checks": [
                        {"id": "choose-one", "status": "ambiguous", "reason": "two valid answers"},
                        {"id": "write-reply", "status": "clear"},
                    ]
                }
            )
        return json.dumps({"checks": [{"id": "write-reply", "status": "unanswerable", "reason": "criterion omitted"}]})

    agent = JobRunner(
        name="reviewer",
        client=FakeLlmClient.from_fn(response),
    )
    with pytest.raises(ExerciseReviewError) as caught:
        verify_exercise_package(lesson, reviewer_agent=agent, strict=True)

    report = caught.value.report
    assert report["status"] == "needs_human"
    assert report["total"] == 2
    assert report["exercise_count"] == 2
    assert report["ambiguous"] == ["choose-one"]
    assert report["unanswerable"] == ["write-reply"]
    assert report["attempt_surface"]["status"] == "needs_human"
    assert report["open_rubrics"]["status"] == "needs_human"


def _reviewer(payload: dict[str, Any] | None = None, error: Exception | None = None) -> object:
    """Build the canonical shared LLM fake behind a structured agent."""
    client = FakeLlmClient.raising(error) if error is not None else FakeLlmClient.responding(json.dumps(payload))
    return JobRunner(name="reviewer", client=client)


def _lesson() -> dict[str, Any]:
    return {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "choose-one",
                "operation": "choose",
                "prompt": [],
                "payload": {
                    "stem": [{"kind": "text", "value": "Choose."}],
                    "options": [
                        {"option_id": "a", "text": "ja"},
                        {"option_id": "b", "text": "nei"},
                    ],
                    "answer_id": "b",
                },
            }
        ]
    }


def _write_lesson() -> dict[str, Any]:
    return {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "write-reply",
                "operation": "write",
                "prompt": [{"kind": "text", "value": "Write a Norwegian reply with a greeting."}],
                "payload": {
                    "response_language": "no",
                    "criteria": [{"id": "greeting", "instruction": "Uses a greeting."}],
                    "judge_prompt": "Check each criterion.",
                },
            }
        ]
    }
