"""Tests for operation content-kind, Bloom, and evidence policy."""

from lesson_builder.domain.lesson.validation.checks.validators.operation_policy import operation_policy_check


def _lesson(*exercises: dict) -> dict:
    return {"elements": list(exercises)}


def _exercise(operation: str, *, bloom: str = "apply", objective: str = "obj-1") -> dict:
    return {
        "element_kind": "exercise",
        "id": f"ex-{operation}",
        "operation": operation,
        "objective_id": objective,
        "bloom_level": bloom,
    }


def test_operation_policy_check_given_grammar_oracle_exercise_expect_objective_evidence_satisfied():
    assert (
        operation_policy_check(
            _lesson(_exercise("build")),
            content_kind="grammar",
        )
        == []
    )


def test_operation_policy_check_given_writing_write_exercise_expect_allowed_judged_operation():
    assert (
        operation_policy_check(
            _lesson(_exercise("write", bloom="apply")),
            content_kind="writing",
        )
        == []
    )


def test_operation_policy_check_given_grammar_write_exercise_expect_evidence_blocker():
    results = operation_policy_check(
        _lesson(_exercise("write")),
        content_kind="grammar",
    )

    assert {result.check_id for result in results} == {"objective_evidence"}


def test_operation_policy_check_given_named_two_speaker_dialogue_expect_no_dialogue_blocker():
    lesson = {
        "elements": [
            {
                "element_kind": "section",
                "id": "model",
                "blocks": [
                    {
                        "kind": "reading",
                        "dialogue_id": "directions-1",
                        "speaker_id": "anna",
                        "speaker_name": "Anna",
                    },
                    {
                        "kind": "reading",
                        "dialogue_id": "directions-1",
                        "speaker_id": "ola",
                        "speaker_name": "Ola",
                    },
                ],
            }
        ]
    }

    assert not any(
        result.check_id == "communicative_dialogue"
        for result in operation_policy_check(lesson, content_kind="communicative")
    )


def test_operation_policy_check_given_single_speaker_reading_expect_dialogue_blocker():
    lesson = {
        "elements": [
            {
                "element_kind": "section",
                "id": "model",
                "blocks": [
                    {
                        "kind": "reading",
                        "dialogue_id": "directions-1",
                        "speaker_id": "anna",
                        "speaker_name": "Anna",
                    }
                ],
            }
        ]
    }

    results = operation_policy_check(lesson, content_kind="communicative")

    assert any(result.check_id == "communicative_dialogue" for result in results)
