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
            "articulation_cue",
            "contrast_to_utterance",
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
        "verdict": "pass",
        "summary": "The exact package is correct, focused, and teachable.",
        "scores": [{"axis": axis, "score": 2, "rationale": f"The package satisfies {axis}."} for axis in axes],
        "findings": [],
    }


def needs_repair_quality_review() -> dict[str, Any]:
    """Return a complete contract-valid review that demands one bounded repair."""
    review = valid_quality_review("grammar")
    review["verdict"] = "needs_repair"
    review["summary"] = "The draft changes the meaning of one translation."
    review["findings"] = [
        {
            "code": "meaning_changing_translation",
            "severity": "blocking",
            "artifact": "lesson.md",
            "location": "first examples block",
            "evidence": "The draft translates 'Snakker du norsk?' as a statement.",
            "repair_instruction": "Restore the faithful English question translation.",
        }
    ]
    return review


__all__ = [
    "lesson_with_exercises",
    "needs_repair_quality_review",
    "valid_exercise",
    "valid_quality_review",
]
