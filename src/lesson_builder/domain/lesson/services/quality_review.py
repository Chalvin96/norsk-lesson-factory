"""Entry points: `validate_quality_review` and `validate_preservation_review`.

Defines the strict, tool-free exact-package review contract used after lesson
authoring. The model judges linguistic and pedagogical quality; deterministic
code only validates rubric completeness, pass arithmetic, and preservation
finding shape. It does not pretend to judge whether Norwegian is idiomatic.
"""

from __future__ import annotations

from typing import Literal

from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.quality_review import QualityReviewValidation

# ── Quality rubric policy ─────────────────────────────────────────────────

K_QUALITY_REVIEW_PASS_SCORE = 16
K_QUALITY_REVIEW_COMMON_AXES: tuple[str, ...] = (
    "observable_decision",
    "meaning_before_form",
    "bokmal_and_translation",
    "cefr_scope",
    "terminology",
    "practice_progression",
    "retrieval_or_transfer",
)
K_QUALITY_REVIEW_CATEGORY_AXES: dict[str, tuple[str, ...]] = {
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


def quality_axes_for_kind(kind: str) -> tuple[str, ...]:
    """Return the exact seven shared plus three category-specific review axes."""
    try:
        category_axes = K_QUALITY_REVIEW_CATEGORY_AXES[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported lesson kind for quality review: {kind!r}") from exc
    return (*K_QUALITY_REVIEW_COMMON_AXES, *category_axes)


def validate_quality_review(
    review: LessonQualityReview,
    expected_axes: tuple[str, ...],
) -> QualityReviewValidation:
    """Require every axis exactly once and enforce the configured pass rule."""
    errors: list[str] = []
    axes = [score.axis for score in review.scores]
    missing = [axis for axis in expected_axes if axis not in axes]
    duplicates = sorted({axis for axis in axes if axes.count(axis) > 1})
    unknown = sorted(set(axes) - set(expected_axes))
    if missing:
        errors.append("review omitted rubric axes: " + ", ".join(missing))
    if duplicates:
        errors.append("review duplicated rubric axes: " + ", ".join(duplicates))
    if unknown:
        errors.append("review returned unknown rubric axes: " + ", ".join(unknown))
    total_score = sum(score.score for score in review.scores)
    has_zero = any(score.score == 0 for score in review.scores)
    has_material_finding = any(finding.severity in {"blocking", "major"} for finding in review.findings)
    expected_verdict: Literal["pass", "needs_repair"] = (
        "pass"
        if not errors and total_score >= K_QUALITY_REVIEW_PASS_SCORE and not has_zero and not has_material_finding
        else "needs_repair"
    )
    if review.verdict != expected_verdict:
        errors.append(
            f"verdict must be {expected_verdict!r} for score {total_score}, zero scores, and material findings"
        )
    return QualityReviewValidation(
        status="invalid" if errors else "valid",
        total_score=total_score,
        expected_verdict=expected_verdict,
        errors=errors,
    )


def quality_review_schema(expected_axes: tuple[str, ...]) -> str:
    """Return the strict reviewer JSON-schema instruction."""
    return (
        "Return one JSON object matching this schema exactly. Include every rubric "
        f"axis exactly once, in this order: {list(expected_axes)!r}. A pass requires at least "
        f"{K_QUALITY_REVIEW_PASS_SCORE}/20, no zero, and no blocking or major "
        f"finding. Schema: {LessonQualityReview.model_json_schema()}"
    )


def validate_preservation_review(
    review: NormalizationPreservationReview,
) -> list[str]:
    """Return structural defects that make a preservation review unusable.

    The Pydantic contract already rejects a contradictory verdict/findings
    pair, so the residual deterministic checks only require non-empty evidence
    for every content-preservation finding.
    """
    errors: list[str] = []
    if review.verdict == "needs_repair":
        if not review.findings:
            errors.append("needs_repair review reported no findings")
        for index, finding in enumerate(review.findings, start=1):
            if not finding.evidence.strip():
                errors.append(f"finding {index} has empty evidence")
    return errors


def preservation_review_schema() -> str:
    """Return the strict preservation-reviewer JSON-schema instruction."""
    return (
        "Return one JSON object matching this schema exactly. `pass` means every "
        "important reviewed teaching unit survived normalization with its meaning "
        "intact. Any dropped or semantically changed rule, example, contrast, "
        "dialogue turn, glossary introduction, or practice checkpoint (handle, "
        "objective, Bloom level, or observable evidence goal) requires "
        "`needs_repair` with one finding per defect. Mechanical compiler syntax, "
        "typed-block validity, and marker/request identity are checked separately "
        "by deterministic validators. Findings restore preserved content only; "
        "this review never authors new lesson content. Schema: "
        f"{NormalizationPreservationReview.model_json_schema()}"
    )


__all__ = [
    "preservation_review_schema",
    "quality_axes_for_kind",
    "quality_review_schema",
    "validate_preservation_review",
    "validate_quality_review",
]
