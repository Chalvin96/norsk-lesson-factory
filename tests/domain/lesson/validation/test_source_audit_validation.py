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


def test_audit_exercise_source_given_uniform_recall_positions_expect_major_finding():
    audit = audit_exercise_source(
        "{{exercise: recall-positions}}\n",
        [_recall_item([0, 0, 0, 0])],
        source_validated=True,
    )

    assert audit.status == "blocked"
    assert any(finding.code == "recall-answer-position-uniform" for finding in audit.material_findings)


def test_audit_exercise_source_given_cyclic_recall_positions_expect_major_finding():
    audit = audit_exercise_source(
        "{{exercise: recall-positions}}\n",
        [_recall_item([1, 2, 0, 1, 2, 0, 1, 2])],
        source_validated=True,
    )

    assert any(finding.code == "recall-answer-position-cyclic" for finding in audit.material_findings)


def test_audit_exercise_source_given_short_near_cycle_expect_no_position_finding():
    audit = audit_exercise_source(
        "{{exercise: recall-positions}}\n",
        [_recall_item([0, 1, 1, 0, 1])],
        source_validated=True,
    )

    assert not any(finding.code.startswith("recall-answer-position-") for finding in audit.findings)


def test_audit_exercise_source_given_varied_recall_positions_expect_clean_answer():
    audit = audit_exercise_source(
        "{{exercise: recall-positions}}\n",
        [_recall_item([2, 0, 1, 1, 0])],
        source_validated=True,
    )

    assert audit.status == "clean"
    assert not any(finding.code.startswith("recall-answer-position-") for finding in audit.findings)


def test_audit_exercise_source_given_two_blank_recall_expect_no_position_finding():
    audit = audit_exercise_source(
        "{{exercise: recall-positions}}\n",
        [_recall_item([0, 0])],
        source_validated=True,
    )

    assert not any(finding.code.startswith("recall-answer-position-") for finding in audit.findings)


def _recall_item(positions: list[int]) -> dict:
    segments: list[dict] = [{"text_md": "Start "}]
    for index, position in enumerate(positions):
        segments.append(
            {
                "blank_id": f"blank-{index}",
                "options": ["alfa", "beta", "gamma"],
                "answer_index": position,
            }
        )
        segments.append({"text_md": " mellom "})
    return {
        "handle": "recall-positions",
        "op": "recall_fill",
        "audio_target": "Alfa beta gamma.",
        "segments": segments,
    }
