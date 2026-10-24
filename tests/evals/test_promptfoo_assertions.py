"""Entry points: deterministic behavior tests for Promptfoo response assertions."""

from __future__ import annotations

import json

from evals.promptfoo.assertions import response_contract
from evals.promptfoo.assertions import validate_normalization_negative_markers
from evals.promptfoo.assertions import validate_review_outcomes
from lesson_builder.domain.lesson.services.review_projection import project_answer_review

K_ANSWER_LESSON = {
    "title": "Bring or have",
    "cefr_level": "A1",
    "elements": [
        {
            "element_kind": "exercise",
            "id": "choose",
            "operation": "choose",
            "prompt": [{"kind": "text", "value": "Choose the phrase for bringing the book."}],
            "payload": {
                "options": [
                    {"option_id": "bring", "text": "Jeg tar med boka."},
                    {"option_id": "have", "text": "Jeg har med boka."},
                ],
                "answer_id": "bring",
            },
        }
    ],
}
K_REVIEW_EDIT_HANDLES = ["label-first", "label-second"]
K_REVIEW_EDIT_CONTEXT = {
    "vars": {
        "surface": "review_edit",
        "expected_exercise_ids": K_REVIEW_EDIT_HANDLES,
        "expected_finding_handles": ["label-first"],
    }
}


def test_response_contract_given_raw_valid_json_expect_schema_pass():
    output = json.dumps({"lesson_md": "# Lesson", "exercise_requests_yaml": "[]"})

    result = response_contract(output, {"vars": {"surface": "normalization"}})

    assert result["pass"] is True
    assert result["score"] == 1.0


def test_response_contract_given_fenced_valid_json_expect_schema_pass():
    output = '```json\n{"lesson_md": "# Lesson", "exercise_requests_yaml": "[]"}\n```'

    result = response_contract(output, {"vars": {"surface": "normalization"}})

    assert result["pass"] is True


def test_response_contract_given_schema_invalid_json_expect_schema_failure():
    result = response_contract('{"lesson_md": "# Lesson"}', {"vars": {"surface": "normalization"}})

    assert result["pass"] is False
    assert "contract" in str(result["reason"])


def test_response_contract_given_non_object_json_expect_schema_failure():
    result = response_contract("[]", {"vars": {"surface": "normalization"}})

    assert result["pass"] is False
    assert "contract" in str(result["reason"])


def test_response_contract_given_unknown_surface_expect_surface_failure():
    result = response_contract("{}", {"vars": {"surface": "not-a-production-surface"}})

    assert result["pass"] is False
    assert "unknown" in str(result["reason"])


def test_validate_normalization_negative_markers_given_typed_incorrect_role_expect_pass():
    output = json.dumps(
        {
            "lesson_md": (
                "::: {.examples teaching_role=incorrect}\n- no: Gi meg en kaffe.\n- en: Give me a coffee.\n:::\n"
            ),
            "exercise_requests_yaml": "[]",
        }
    )

    result = validate_normalization_negative_markers(output, {"vars": {"surface": "normalization"}})

    assert result["pass"] is True


def test_validate_normalization_negative_markers_given_missing_typed_role_expect_failure():
    output = json.dumps(
        {
            "lesson_md": "::: examples\n- no: Gi meg en kaffe.\n- en: Give me a coffee.\n:::\n",
            "exercise_requests_yaml": "[]",
        }
    )

    result = validate_normalization_negative_markers(output, {"vars": {"surface": "normalization"}})

    assert result["pass"] is False
    assert "does not encode" in str(result["reason"])


def test_validate_normalization_negative_markers_given_legacy_inline_marker_expect_failure():
    output = json.dumps(
        {
            "lesson_md": "::: examples\n- no: Gi meg en kaffe.\n- en: Incorrect: Give me a coffee.\n:::\n",
            "exercise_requests_yaml": "[]",
        }
    )

    result = validate_normalization_negative_markers(output, {"vars": {"surface": "normalization"}})

    assert result["pass"] is False
    assert "legacy inline" in str(result["reason"])


def test_validate_review_outcomes_given_blinded_answer_expect_restored_answer_and_issue():
    projection = project_answer_review(K_ANSWER_LESSON)
    review_option_id = next(
        review_id
        for review_id, authored_id in projection.review_to_authored_option_ids["choose"].items()
        if authored_id == "bring"
    )
    assert review_option_id != "bring"
    output = json.dumps(
        {
            "answers": [
                {
                    "id": "choose",
                    "status": "solved",
                    "answer": review_option_id,
                    "semantic_issues": [{"category": "composite_decision_load", "reason": "Four mappings."}],
                }
            ]
        }
    )
    context = {
        "vars": {
            "surface": "answer",
            "lesson": json.dumps(K_ANSWER_LESSON),
            "expected_exercise_ids": ["choose"],
            "expected_statuses": {"choose": "solved"},
            "expected_issue_categories": {"choose": ["composite_decision_load"]},
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is True


def test_validate_review_outcomes_given_other_blinded_answer_expect_answer_failure():
    projection = project_answer_review(K_ANSWER_LESSON)
    review_option_id = next(
        review_id
        for review_id, authored_id in projection.review_to_authored_option_ids["choose"].items()
        if authored_id == "have"
    )
    output = json.dumps({"answers": [{"id": "choose", "status": "solved", "answer": review_option_id}]})
    context = {
        "vars": {
            "surface": "answer",
            "lesson": json.dumps(K_ANSWER_LESSON),
            "expected_exercise_ids": ["choose"],
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "authored answers" in str(result["reason"])


def test_validate_review_outcomes_given_missing_exercise_expect_coverage_failure():
    output = json.dumps({"checks": [{"id": "choose", "status": "clear"}]})
    context = {
        "vars": {
            "surface": "attempt",
            "expected_exercise_ids": ["choose", "missing"],
            "expected_statuses": {"choose": "clear", "missing": "unanswerable"},
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "missing=['missing']" in str(result["reason"])


def test_validate_review_outcomes_given_duplicate_exercise_expect_coverage_failure():
    output = json.dumps(
        {
            "checks": [
                {"id": "choose", "status": "clear"},
                {"id": "choose", "status": "unanswerable"},
            ]
        }
    )
    context = {"vars": {"surface": "attempt", "expected_exercise_ids": ["choose"]}}

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "duplicate" in str(result["reason"])


def test_validate_review_outcomes_given_wrong_status_expect_status_failure():
    output = json.dumps({"checks": [{"id": "choose", "status": "unanswerable"}]})
    context = {
        "vars": {
            "surface": "open_rubric",
            "expected_exercise_ids": ["choose"],
            "expected_statuses": {"choose": "clear"},
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "status mismatch" in str(result["reason"])


def test_validate_review_outcomes_given_arbitrary_finding_code_on_expected_handle_expect_pass():
    output = json.dumps(
        {
            "summary": "One advisory issue.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [
                {
                    "code": "unlisted-finding-code",
                    "severity": "major",
                    "artifact": "exercises.yaml",
                    "location": "handle: label-first; example",
                    "rationale": "Check the source marker.",
                }
            ],
        }
    )

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is True


def test_validate_review_outcomes_given_finding_on_control_handle_expect_handle_failure():
    output = json.dumps(
        {
            "summary": "One advisory issue.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [
                {
                    "code": "unlisted-finding-code",
                    "severity": "major",
                    "artifact": "exercises.yaml",
                    "location": "handle: label-second; example",
                    "rationale": "Check the source marker.",
                }
            ],
        }
    )

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "unexpected=['label-second']" in str(result["reason"])


def test_validate_review_outcomes_given_handle_only_in_lesson_prose_expect_handle_failure():
    context = {
        "vars": {
            "surface": "review_edit",
            "expected_exercise_ids": ["destination"],
            "expected_finding_handles": ["destination"],
        }
    }
    output = json.dumps(
        {
            "summary": "One lesson prose concern.",
            "audited_handles": ["destination"],
            "findings": [
                {
                    "code": "unlisted-finding-code",
                    "severity": "major",
                    "artifact": "lesson.md",
                    "location": "The prose mentions destination in this example.",
                    "rationale": "The lesson prose discusses this word.",
                }
            ],
        }
    )

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "missing=['destination']" in str(result["reason"])


def test_validate_review_outcomes_given_no_findings_for_expected_handle_expect_handle_failure():
    output = json.dumps(
        {
            "summary": "No concern found.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [],
        }
    )

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "missing=['label-first']" in str(result["reason"])


def test_validate_review_outcomes_given_duplicate_review_edit_handle_expect_coverage_failure():
    output = json.dumps(
        {
            "summary": "No concern found.",
            "audited_handles": [*K_REVIEW_EDIT_HANDLES, K_REVIEW_EDIT_HANDLES[0]],
            "findings": [],
        }
    )

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "duplicate" in str(result["reason"])


def test_validate_review_outcomes_given_wrong_target_issue_category_expect_category_failure():
    output = _lesson_review_output(_lesson_finding("pedagogy_gap"))

    result = validate_review_outcomes(output, _lesson_review_context(["incorrect_claim"]))

    assert result["pass"] is False
    assert "no major finding" in str(result["reason"])


def test_validate_review_outcomes_given_unsupported_claim_on_planted_target_expect_pass():
    output = _lesson_review_output(_lesson_finding("unsupported_claim"))

    result = validate_review_outcomes(output, _lesson_review_context(["incorrect_claim", "unsupported_claim"]))

    assert result["pass"] is True


def test_validate_review_outcomes_given_duplicate_planted_findings_expect_pass():
    output = _lesson_review_output(_lesson_finding("incorrect_claim"), _lesson_finding("incorrect_claim"))

    result = validate_review_outcomes(output, _lesson_review_context(["incorrect_claim"]))

    assert result["pass"] is True


def test_validate_review_outcomes_given_planted_finding_only_in_exercises_expect_recall_failure():
    output = _lesson_review_output(_lesson_finding("incorrect_claim", artifact="exercises.yaml"))

    result = validate_review_outcomes(output, _lesson_review_context(["incorrect_claim"]))

    assert result["pass"] is False


def test_validate_review_outcomes_given_minor_finding_on_clean_target_expect_pass():
    output = _lesson_review_output(_lesson_finding("copy_issue", severity="minor"))

    result = validate_review_outcomes(output, _lesson_review_context([]))

    assert result["pass"] is True


def test_validate_review_outcomes_given_major_finding_on_clean_target_expect_false_alarm_failure():
    output = _lesson_review_output(_lesson_finding("incorrect_claim"))

    result = validate_review_outcomes(output, _lesson_review_context([]))

    assert result["pass"] is False
    assert "clean target" in str(result["reason"])


def test_validate_review_outcomes_given_planted_preservation_category_with_extra_expect_pass():
    output = json.dumps(
        {
            "summary": "Two possible preservation losses.",
            "findings": [
                {"category": "dropped_contrast", "rationale": "The meaning distinction is missing."},
                {"category": "dropped_example", "rationale": "One example is missing."},
            ],
        }
    )
    context = {"vars": {"surface": "preservation_review", "expected_issue_categories": ["dropped_contrast"]}}

    result = validate_review_outcomes(output, context)

    assert result["pass"] is True


def test_validate_review_outcomes_given_missing_planted_preservation_category_expect_failure():
    output = json.dumps(
        {
            "summary": "A possible preservation loss.",
            "findings": [{"category": "dropped_example", "rationale": "One example is missing."}],
        }
    )
    context = {"vars": {"surface": "preservation_review", "expected_issue_categories": ["dropped_contrast"]}}

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "issue categories mismatch" in str(result["reason"])


def test_validate_review_outcomes_given_control_preservation_category_expect_failure():
    output = json.dumps(
        {
            "summary": "A possible preservation loss.",
            "findings": [{"category": "dropped_contrast", "rationale": "The meaning distinction is missing."}],
        }
    )
    context = {"vars": {"surface": "preservation_review", "expected_issue_categories": []}}

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "issue categories mismatch" in str(result["reason"])


def test_validate_review_outcomes_given_standalone_verdict_expect_pass():
    output = json.dumps({"results": [{"id": "say-name", "verdict": "standalone"}]})
    context = {
        "vars": {
            "surface": "standalone_review",
            "expected_exercise_ids": ["say-name"],
            "expected_statuses": {"say-name": "standalone"},
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is True


def test_validate_review_outcomes_given_wrong_standalone_verdict_expect_failure():
    output = json.dumps({"results": [{"id": "say-name", "verdict": "context_dependent"}]})
    context = {
        "vars": {
            "surface": "standalone_review",
            "expected_exercise_ids": ["say-name"],
            "expected_statuses": {"say-name": "standalone"},
        }
    }

    result = validate_review_outcomes(output, context)

    assert result["pass"] is False
    assert "status mismatch" in str(result["reason"])


def test_validate_review_outcomes_given_minor_finding_on_planted_handle_expect_handle_failure():
    output = _review_edit_output(_review_edit_finding("label-first", severity="minor"))

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "missing=['label-first']" in str(result["reason"])


def test_validate_review_outcomes_given_minor_finding_on_control_handle_expect_pass():
    output = _review_edit_output(
        _review_edit_finding("label-first", severity="major"),
        _review_edit_finding("label-second", severity="minor"),
    )

    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is True


def test_validate_review_outcomes_given_malformed_response_expect_protocol_failure_left_to_contract():
    output = '{"findings": "not a list"}'
    result = validate_review_outcomes(output, K_REVIEW_EDIT_CONTEXT)
    contract = response_contract(output, {"vars": {"surface": "review_edit"}})

    assert result["pass"] is True
    assert result["score"] == 0.0
    assert "protocol failure; scored under response_contract" in str(result["reason"])
    assert contract["pass"] is False
    assert contract["score"] == 0.0


def _lesson_review_output(*findings: dict[str, str]) -> str:
    return json.dumps(
        {
            "summary": "A source concern.",
            "scores": [{"axis": "observable_decision", "score": 1, "rationale": "The task is visible."}],
            "findings": list(findings),
        }
    )


def _lesson_finding(code: str, *, severity: str = "major", artifact: str = "lesson.md") -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "artifact": artifact,
        "location": "known rule",
        "evidence": "The present tense rule is explicit.",
        "rationale": "The task may need another example.",
    }


def _lesson_review_context(accepted: list[str]) -> dict[str, object]:
    return {
        "vars": {
            "surface": "lesson_review",
            "expected_target_anchor": "present tense",
            "accepted_issue_categories": accepted,
        }
    }


def _review_edit_output(*findings: dict[str, str]) -> str:
    return json.dumps(
        {"summary": "Advisory issues.", "audited_handles": K_REVIEW_EDIT_HANDLES, "findings": list(findings)}
    )


def _review_edit_finding(
    handle: str,
    *,
    severity: str,
    artifact: str = "exercises.yaml",
) -> dict[str, str]:
    return {
        "code": "unlisted-finding-code",
        "severity": severity,
        "artifact": artifact,
        "location": f"handle: {handle}; example",
        "rationale": "Check the source marker.",
    }
