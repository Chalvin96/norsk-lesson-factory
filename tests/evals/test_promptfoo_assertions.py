"""Entry points: tests for the Promptfoo response and review/edit assertions."""

from __future__ import annotations

import json

from evals.promptfoo.assertions import response_contract
from evals.promptfoo.assertions import validate_review_edit_handle_coverage
from evals.promptfoo.assertions import validate_review_edit_replacements

K_NORMALIZATION_CONTEXT = {"vars": {"surface": "normalization"}}
K_REVIEW_EDIT_HANDLES = ["label-first", "label-second"]
K_REVIEW_EDIT_CONTEXT = {
    "vars": {
        "surface": "review_edit",
        "mechanical_audit": json.dumps(
            {
                "status": "clean",
                "exercise_handles": K_REVIEW_EDIT_HANDLES,
                "marker_handles": K_REVIEW_EDIT_HANDLES,
            }
        ),
        "lesson_md": "- en: Intended: I need one bag.\n- en: Intended: She buys bread.\n",
        "exercises_yaml": "- handle: label-first\n  op: choose\n",
    }
}


def test_response_contract_given_raw_valid_json_expect_pass():
    output = json.dumps({"lesson_md": "# Lesson", "exercise_requests_yaml": "[]"})

    result = response_contract(output, K_NORMALIZATION_CONTEXT)

    assert result["pass"] is True
    assert result["score"] == 1.0


def test_response_contract_given_fenced_valid_json_expect_pass():
    output = '```json\n{"lesson_md": "# Lesson", "exercise_requests_yaml": "[]"}\n```'

    result = response_contract(output, K_NORMALIZATION_CONTEXT)

    assert result["pass"] is True


def test_response_contract_given_schema_invalid_json_expect_fail():
    result = response_contract('{"lesson_md": "# Lesson"}', K_NORMALIZATION_CONTEXT)

    assert result["pass"] is False
    assert "contract" in str(result["reason"])


def test_response_contract_given_non_object_json_expect_fail():
    result = response_contract("[]", K_NORMALIZATION_CONTEXT)

    assert result["pass"] is False
    assert "contract" in str(result["reason"])


def test_response_contract_given_unknown_surface_expect_fail():
    result = response_contract("{}", {"vars": {"surface": "not-a-production-surface"}})

    assert result["pass"] is False
    assert "unknown" in str(result["reason"])


def test_validate_review_edit_handle_coverage_given_complete_unique_handles_expect_pass():
    output = json.dumps(
        {
            "verdict": "pass",
            "summary": "No material finding.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [],
            "edits": [],
        }
    )

    result = validate_review_edit_handle_coverage(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is True


def test_validate_review_edit_handle_coverage_given_duplicate_handle_expect_fail():
    output = json.dumps(
        {
            "verdict": "pass",
            "summary": "No material finding.",
            "audited_handles": [*K_REVIEW_EDIT_HANDLES, K_REVIEW_EDIT_HANDLES[0]],
            "findings": [],
            "edits": [],
        }
    )

    result = validate_review_edit_handle_coverage(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "duplicate" in str(result["reason"])


def test_validate_review_edit_handle_coverage_given_missing_handle_expect_fail():
    output = json.dumps(
        {
            "verdict": "pass",
            "summary": "No material finding.",
            "audited_handles": K_REVIEW_EDIT_HANDLES[:-1],
            "findings": [],
            "edits": [],
        }
    )

    result = validate_review_edit_handle_coverage(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "missing" in str(result["reason"])


def test_validate_review_edit_replacements_given_exact_replacement_expect_pass():
    output = json.dumps(
        {
            "verdict": "needs_edit",
            "summary": "Relabel both negative-example glosses.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [
                {
                    "code": "negative-example-label",
                    "severity": "major",
                    "artifact": "lesson.md",
                    "location": "example glosses",
                    "evidence": "Intended:",
                    "explanation": "The compiler needs the recognized negative label.",
                }
            ],
            "edits": [
                {
                    "finding_code": "negative-example-label",
                    "artifact": "lesson.md",
                    "old_text": "Intended:",
                    "new_text": "Intended meaning:",
                    "expected_occurrences": 2,
                    "reason": "Use the recognized negative-example label.",
                }
            ],
        }
    )

    result = validate_review_edit_replacements(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is True


def test_validate_review_edit_replacements_given_stale_replacement_expect_fail():
    output = json.dumps(
        {
            "verdict": "needs_edit",
            "summary": "Relabel both negative-example glosses.",
            "audited_handles": K_REVIEW_EDIT_HANDLES,
            "findings": [
                {
                    "code": "negative-example-label",
                    "severity": "major",
                    "artifact": "lesson.md",
                    "location": "example glosses",
                    "evidence": "Intended:",
                    "explanation": "The compiler needs the recognized negative label.",
                }
            ],
            "edits": [
                {
                    "finding_code": "negative-example-label",
                    "artifact": "lesson.md",
                    "old_text": "Intended:",
                    "new_text": "Intended meaning:",
                    "expected_occurrences": 1,
                    "reason": "Use the recognized negative-example label.",
                }
            ],
        }
    )

    result = validate_review_edit_replacements(output, K_REVIEW_EDIT_CONTEXT)

    assert result["pass"] is False
    assert "expected 1" in str(result["reason"])
