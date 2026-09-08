"""Behavior tests for the deterministic recall model-audio language gate."""

from lesson_builder.domain.lesson.validation.checks.validators.recall_audio_language import recall_audio_language_check


def test_recall_audio_language_check_given_norwegian_target_expect_no_findings() -> None:
    results = recall_audio_language_check({"elements": [_exercise("Snakker du norsk?")]})

    assert results == []


def test_recall_audio_language_check_given_english_target_expect_blocker() -> None:
    results = recall_audio_language_check({"elements": [_exercise("Please snakker du norsk?")]})

    assert [(result.check_id, result.unit_id, result.severity) for result in results] == [
        ("recall_audio_language", "recall-question", "blocker")
    ]
    assert "please" in results[0].message


def _exercise(audio_target: str) -> dict[str, object]:
    """Build the minimal parsed recall exercise shape for the gate."""
    return {
        "element_kind": "exercise",
        "id": "recall-question",
        "operation": "recall_fill",
        "payload": {"audio_target": audio_target},
    }
