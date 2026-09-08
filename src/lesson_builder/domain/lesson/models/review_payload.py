"""Not a check itself — typed projections and reversibility metadata for lesson review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ExerciseQuestion = dict[str, Any]


@dataclass(frozen=True, slots=True)
class AnswerReviewProjection:
    """Keyless answer-review questions plus their reversible option-ID map."""

    questions: list[ExerciseQuestion]
    review_to_authored_option_ids: dict[str, dict[str, str]]


__all__ = ["AnswerReviewProjection"]
