"""Entry points: `quality_axes_for_kind`, `quality_review_schema`, `preservation_review_schema`, and `validate_quality_review`."""

from __future__ import annotations

from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.quality_review import QualityReviewValidation
from lesson_builder.domain.lesson.settings import K_LESSON_QUALITY_REVIEW_CATEGORY_AXES
from lesson_builder.domain.lesson.settings import K_LESSON_QUALITY_REVIEW_COMMON_AXES


def quality_axes_for_kind(kind: str) -> tuple[str, ...]:
    """Return the seven shared and three category-specific review axes."""
    try:
        category_axes = K_LESSON_QUALITY_REVIEW_CATEGORY_AXES[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported lesson kind for quality review: {kind!r}") from exc
    return (*K_LESSON_QUALITY_REVIEW_COMMON_AXES, *category_axes)


def validate_quality_review(
    review: LessonQualityReview,
    expected_axes: tuple[str, ...],
) -> QualityReviewValidation:
    """Require every configured rubric axis exactly once."""
    axes = [score.axis for score in review.scores]
    missing = [axis for axis in expected_axes if axis not in axes]
    duplicates = sorted({axis for axis in axes if axes.count(axis) > 1})
    unknown = sorted(set(axes) - set(expected_axes))
    errors = []
    if missing:
        errors.append("review omitted rubric axes: " + ", ".join(missing))
    if duplicates:
        errors.append("review duplicated rubric axes: " + ", ".join(duplicates))
    if unknown:
        errors.append("review returned unknown rubric axes: " + ", ".join(unknown))
    return QualityReviewValidation(
        status="invalid" if errors else "valid",
        total_score=sum(score.score for score in review.scores),
        errors=errors,
    )


def quality_review_schema(expected_axes: tuple[str, ...]) -> str:
    """Return the advisory reviewer JSON-schema instruction."""
    return (
        "Return one JSON object matching this schema exactly. Include every rubric "
        f"axis exactly once, in this order: {list(expected_axes)!r}. Score each axis "
        "from 0 to 2; scores are descriptive and do not determine approval. "
        "All findings are unverified AI suggestions. Include a location and rationale. "
        "Findings never authorize edits, require "
        "owner action, or block approval. Schema: "
        f"{LessonQualityReview.model_json_schema()}"
    )


def preservation_review_schema() -> str:
    """Return the advisory preservation-reviewer JSON-schema instruction."""
    return (
        "Return one JSON object matching this schema exactly. Compare the reviewed "
        "draft and normalized source for possible losses of rules, examples, "
        "contrasts, dialogue turns, glossary introductions, and practice intent. "
        "Put each finding's location inside its `rationale`. "
        "Every finding is an unverified AI suggestion: it does not "
        "authorize edits, require owner action, or block the package. Deterministic "
        "source, compiler, marker, and identity checks own blocking failures. Schema: "
        f"{NormalizationPreservationReview.model_json_schema()}"
    )


__all__ = [
    # Review contracts.
    "preservation_review_schema",
    "quality_axes_for_kind",
    "quality_review_schema",
    "validate_quality_review",
]
