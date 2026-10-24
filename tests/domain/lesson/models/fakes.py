"""Entry points: `valid_exercise`, `lesson_with_exercises`, and quality-review fakes build typed test values."""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.lesson import Lesson


def valid_exercise(
    operation: str,
    *,
    payload: dict[str, object],
    exercise_id: str = "exercise-1",
    objective_id: str = "obj-1",
    bloom_level: str = "remember",
) -> dict[str, object]:
    """Build the shared envelope around a behavior-specific exercise payload."""
    return {
        "element_kind": "exercise",
        "id": exercise_id,
        "operation": operation,
        "objective_id": objective_id,
        "bloom_level": bloom_level,
        "prompt": [],
        "explanation": None,
        "derived_from": [],
        "payload": payload,
    }


def lesson_with_exercises(exercises: list[dict[str, object]], *, objective_ids: tuple[str, ...] = ("obj-1",)) -> Lesson:
    """Build a valid Lesson around the exact exercise values under test."""
    objectives = [
        {"id": objective_id, "statement": objective_id, "bloom_targets": ["remember"]} for objective_id in objective_ids
    ]
    return Lesson.model_validate(
        {
            "key": "exercise-diagnostics",
            "concept_slug": "exercise-diagnostics",
            "grounding_mode": "grounded",
            "title": "Exercise diagnostics",
            "cefr_level": "A1",
            "goal": "Inspect authored exercise evidence.",
            "objectives": objectives,
            "elements": exercises,
            "review_pool": {"pools": []},
        }
    )


def valid_quality_review(kind: str = "grammar") -> dict[str, Any]:
    """Return a complete passing exact-package quality review."""
    common_axes = (
        "observable_decision",
        "meaning_before_form",
        "bokmal_and_translation",
        "cefr_scope",
        "terminology",
        "practice_progression",
        "retrieval_or_transfer",
    )
    category_axes = {
        "grammar": (
            "compact_generalization",
            "aligned_form_meaning_contrast",
            "misconception_resolution",
        ),
        "phraseology": (
            "whole_unit_meaning",
            "collocational_boundary",
            "non_interchangeability",
        ),
        "pronunciation": (
            "perception_accuracy",
            "sound_spelling_link",
            "contrast_in_context",
        ),
        "communicative": (
            "speech_act_purpose",
            "turn_choice_consequence",
            "repair_and_transfer",
        ),
        "writing": (
            "model_text_fitness",
            "organization_language_choices",
            "revision_guidance",
        ),
    }
    axes = (*common_axes, *category_axes[kind])
    return {
        "summary": "The exact package is correct, focused, and teachable.",
        "scores": [{"axis": axis, "score": 2, "rationale": f"The package satisfies {axis}."} for axis in axes],
        "findings": [],
    }


def advisory_quality_review() -> dict[str, Any]:
    """Return a complete review with one unverified advisory finding."""
    review = valid_quality_review("grammar")
    review["summary"] = "The draft changes the meaning of one translation."
    review["findings"] = [
        {
            "code": "meaning_changing_translation",
            "severity": "major",
            "artifact": "lesson.md",
            "location": "first examples block",
            "evidence": "The Norwegian question uses a different meaning from its translation.",
            "rationale": "Check the translation against the intended question meaning.",
        }
    ]
    return review


__all__ = [
    "lesson_with_exercises",
    "advisory_quality_review",
    "valid_exercise",
    "valid_quality_review",
]
