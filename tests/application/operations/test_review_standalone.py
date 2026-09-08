"""Behavior tests for the blocking standalone exercise-quality review."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lesson_builder.application.operations.review_standalone import StandaloneReviewError
from lesson_builder.application.operations.review_standalone import build_standalone_review_prompt
from lesson_builder.application.operations.review_standalone import collect_deterministic_standalone_findings
from lesson_builder.application.operations.review_standalone import project_standalone_review_payload
from lesson_builder.application.operations.review_standalone import review_standalone_exercises
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.domain.lesson.validation.standalone_references import scan_unresolved_references
from tests.clients.llm.fakes import FakeLlmClient


def test_project_standalone_review_payload_given_derived_from_exercise_expect_no_lesson_context():
    payload = project_standalone_review_payload(_build_lesson())

    assert [question["id"] for question in payload] == ["context-dependent-write", "standalone-speak"]
    assert all("lesson_context" not in question for question in payload)
    assert all("derived_from" not in question for question in payload)
    write_question = payload[0]
    assert write_question["operation"] == "write"
    assert "criteria" not in write_question
    assert "judge_prompt" not in write_question
    speak_question = payload[1]
    assert speak_question["operation"] == "speak"
    assert "target" not in speak_question


def test_build_standalone_review_prompt_given_payload_expect_no_section_projection():
    payload = project_standalone_review_payload(_build_lesson())

    prompt = build_standalone_review_prompt(payload)

    assert "At the café" not in prompt
    assert "Jeg vil gjerne ha en kaffe." not in prompt
    assert "Return to the dialogue and rewrite Sara's first turn." in prompt
    assert json.dumps(payload, ensure_ascii=False) in prompt


def test_review_standalone_exercises_given_context_dependent_payload_expect_strict_failure():
    with pytest.raises(StandaloneReviewError) as caught:
        review_standalone_exercises(
            _build_lesson(),
            reviewer_agent=_build_reviewer(
                {
                    "results": [
                        {
                            "id": "context-dependent-write",
                            "verdict": "context_dependent",
                            "reason": "The payload refers to a dialogue that is not restated.",
                        },
                        {"id": "standalone-speak", "verdict": "standalone"},
                    ]
                }
            ),
            strict=True,
        )

    report = caught.value.report
    assert report["status"] == "needs_human"
    assert report["context_dependent"] == ["context-dependent-write"]


def test_review_standalone_exercises_given_locally_complete_payload_expect_pass():
    lesson = _build_lesson()
    lesson["elements"][1]["prompt"] = [
        {"kind": "text", "value": "Write one Norwegian order for tea using «Jeg vil gjerne ha»."}
    ]

    report = review_standalone_exercises(
        lesson,
        reviewer_agent=_build_reviewer(
            {
                "results": [
                    {"id": "context-dependent-write", "verdict": "standalone"},
                    {"id": "standalone-speak", "verdict": "standalone"},
                ]
            }
        ),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["total"] == 2
    assert report["context_dependent"] == []


def test_review_standalone_exercises_given_deterministic_finding_expect_failure_even_when_model_passes():
    with pytest.raises(StandaloneReviewError) as caught:
        review_standalone_exercises(
            _build_lesson(),
            reviewer_agent=_build_reviewer(
                {
                    "results": [
                        {"id": "context-dependent-write", "verdict": "standalone"},
                        {"id": "standalone-speak", "verdict": "standalone"},
                    ]
                }
            ),
            strict=True,
        )

    findings = caught.value.report["deterministic_findings"]
    assert findings
    assert {finding["id"] for finding in findings} == {"context-dependent-write"}


def test_review_standalone_exercises_given_reviewer_outage_expect_strict_failure():
    with pytest.raises(StandaloneReviewError):
        review_standalone_exercises(
            _build_lesson(),
            reviewer_agent=_build_reviewer(error=BackendDownException("no backend")),
            strict=True,
        )


def test_review_standalone_exercises_given_incomplete_reviewer_coverage_expect_structured_invalid_report():
    report = review_standalone_exercises(
        _build_lesson(),
        reviewer_agent=_build_reviewer({"results": [{"id": "standalone-speak", "verdict": "standalone"}]}),
    )

    assert report["status"] == "reviewer_invalid"
    assert "exactly one verdict per exercise" in report["error"]
    with pytest.raises(StandaloneReviewError):
        review_standalone_exercises(
            _build_lesson(),
            reviewer_agent=_build_reviewer({"results": [{"id": "standalone-speak", "verdict": "standalone"}]}),
            strict=True,
        )


def test_review_standalone_exercises_given_no_exercises_expect_pass_without_model():
    report = review_standalone_exercises(
        {"elements": []},
        reviewer_agent=_build_reviewer(error=BackendDownException("no backend")),
        strict=True,
    )

    assert report["status"] == "pass"
    assert report["total"] == 0


def test_scan_unresolved_references_given_backward_instruction_expect_finding():
    findings = scan_unresolved_references({"prompt_md": "Return to the café scene and answer Sara again."})

    assert [finding["kind"] for finding in findings] == ["return_to"]


def test_scan_unresolved_references_given_unrestated_dialogue_reference_expect_finding():
    findings = scan_unresolved_references(
        {"prompt_md": "Which explanation best matches the dialogue?"},
        restatement_text="There is no quoted material here.",
    )

    assert [finding["kind"] for finding in findings] == ["dialogue_reference"]


def test_scan_unresolved_references_given_locally_restated_dialogue_expect_no_finding():
    findings = scan_unresolved_references(
        {"prompt_md": "Answer the dialogue question «Vil du ha melk?» with your own choice."},
        restatement_text="Answer the dialogue question «Vil du ha melk?»",
    )

    assert findings == []


def test_scan_unresolved_references_given_colon_restatement_expect_no_finding():
    findings = scan_unresolved_references(
        {"prompt_md": "Use this café context from the lesson: Jonas has checked the figures."}
    )

    assert findings == []


def test_scan_unresolved_references_given_ordinary_prose_expect_no_finding():
    findings = scan_unresolved_references(
        {
            "prompt_md": "Write two Norwegian sentences ordering «en kopp te» and «et rundstykke med ost».",
            "stem_md": "Which sentence asks for a large coffee?",
        }
    )

    assert findings == []


def test_collect_deterministic_standalone_findings_given_write_reference_above_expect_finding():
    payload = [
        {
            "id": "replace-above",
            "operation": "write",
            "prompt": "Replace the items above with two other café items.",
            "criteria": [],
        }
    ]

    findings = collect_deterministic_standalone_findings(payload)

    assert [finding["kind"] for finding in findings] == ["above_reference"]
    assert findings[0]["id"] == "replace-above"


def _build_lesson() -> dict[str, Any]:
    return {
        "elements": [
            {
                "element_kind": "section",
                "id": "cafe-dialogue",
                "title": "At the café",
                "role": "model",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [{"kind": "text", "value": "Jeg vil gjerne ha en kaffe."}],
                        "translation": "I would like a coffee.",
                        "speaker_name": "Sara",
                    }
                ],
            },
            {
                "element_kind": "exercise",
                "id": "context-dependent-write",
                "operation": "write",
                "derived_from": [{"section_id": "cafe-dialogue"}],
                "prompt": [{"kind": "text", "value": "Return to the dialogue and rewrite Sara's first turn."}],
                "payload": {
                    "response_language": "no",
                    "min_words": 5,
                    "max_words": 20,
                    "judge_prompt": "Judge the rewrite.",
                    "criteria": [{"id": "frame", "instruction": "Uses the taught request frame."}],
                },
            },
            {
                "element_kind": "exercise",
                "id": "standalone-speak",
                "operation": "speak",
                "prompt": [{"kind": "text", "value": "Say «Jeg vil gjerne ha en kopp te.» out loud."}],
                "payload": {"target": "Jeg vil gjerne ha en kopp te."},
            },
        ]
    }


def _build_reviewer(payload: dict[str, Any] | None = None, error: Exception | None = None) -> JobRunner:
    client = FakeLlmClient.raising(error) if error is not None else FakeLlmClient.responding(json.dumps(payload))
    return JobRunner(name="reviewer", client=client)
