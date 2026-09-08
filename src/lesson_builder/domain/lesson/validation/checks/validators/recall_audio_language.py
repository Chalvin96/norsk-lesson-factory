"""Entry point: `recall_audio_language_check` validates recall audio targets."""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.services.audio_language import collect_non_norwegian_audio_words


def recall_audio_language_check(data: dict[str, Any]) -> list[CheckResult]:
    """Block recall targets containing known non-Norwegian audio words."""
    results: list[CheckResult] = []
    for exercise in data.get("elements", []):
        if not isinstance(exercise, dict) or exercise.get("element_kind") != "exercise":
            continue
        if exercise.get("operation") != "recall_fill":
            continue
        payload = exercise.get("payload")
        if not isinstance(payload, dict):
            continue
        target = payload.get("audio_target")
        if not isinstance(target, str):
            continue
        foreign_words = collect_non_norwegian_audio_words(target)
        if foreign_words:
            results.append(
                CheckResult(
                    check_id="recall_audio_language",
                    severity="blocker",
                    unit_id=str(exercise.get("id", "recall_fill")),
                    message=(
                        "recall_fill audio_target contains known non-Norwegian word(s): " + ", ".join(foreign_words)
                    ),
                    fix_hint="Rewrite audio_target as the exact Norwegian utterance used for model audio.",
                )
            )
    return results


__all__ = ["recall_audio_language_check"]
