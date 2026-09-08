"""Behavior tests for the filesystem-free authored-source validator."""

from __future__ import annotations

from lesson_builder.domain.lesson.validation.source_audit import audit_exercise_source


def test_audit_exercise_source_given_parsed_build_source_expect_clean_answer():
    items = [
        {
            "handle": "build-greeting",
            "op": "build",
            "tokens": [
                {"token_id": "subject", "text": "Jeg"},
                {"token_id": "verb", "text": "jobber"},
                {"token_id": "mark", "text": "."},
            ],
            "answer_order": ["subject", "verb", "mark"],
        }
    ]

    audit = audit_exercise_source(
        "{{exercise: build-greeting}}\n",
        items,
        source_validated=True,
    )

    assert audit.status == "clean"
    assert [answer.text for answer in audit.built_answers] == ["Jeg jobber."]


def test_audit_exercise_source_given_marker_handle_mismatch_expect_blocking_finding():
    items = [{"handle": "build-greeting", "op": "build"}]

    audit = audit_exercise_source(
        "{{exercise: other-greeting}}\n",
        items,
        source_validated=True,
    )

    assert audit.status == "blocked"
    assert any(finding.code == "exercise-marker-handle-mismatch" for finding in audit.findings)
