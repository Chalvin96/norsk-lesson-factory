"""Not a check itself — quality-review contracts and their local invariants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

QualityAxis = Literal[
    "observable_decision",
    "meaning_before_form",
    "compact_generalization",
    "aligned_form_meaning_contrast",
    "bokmal_and_translation",
    "cefr_scope",
    "misconception_resolution",
    "terminology",
    "practice_progression",
    "retrieval_or_transfer",
    "whole_unit_meaning",
    "collocational_boundary",
    "non_interchangeability",
    "perception_accuracy",
    "sound_spelling_link",
    "contrast_in_context",
    "speech_act_purpose",
    "turn_choice_consequence",
    "repair_and_transfer",
    "model_text_fitness",
    "organization_language_choices",
    "revision_guidance",
]
QualitySeverity = Literal["major", "minor"]
QualityFindingCode = Literal[
    "incorrect_claim",
    "unsupported_claim",
    "unnatural_bokmal",
    "meaning_changing_translation",
    "accepted_form_marked_wrong",
    "ambiguous_answer_key",
    "target_loss",
    "prerequisite_scope_breach",
    "compiler_failure",
    "pedagogy_gap",
    "terminology_gap",
    "practice_gap",
    "copy_issue",
]
QualityArtifact = Literal["brief.yaml", "lesson.md", "exercises.yaml"]


class QualityAxisScore(BaseModel):
    """One complete 0–2 rubric judgment with an explanatory rationale."""

    model_config = ConfigDict(extra="forbid")

    axis: QualityAxis
    score: int = Field(ge=0, le=2)
    rationale: str = Field(min_length=1)


class QualityFinding(BaseModel):
    """One source-grounded, unverified advisory concern for author inspection."""

    model_config = ConfigDict(extra="forbid")

    code: QualityFindingCode
    severity: QualitySeverity
    artifact: QualityArtifact
    location: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class LessonQualityReview(BaseModel):
    """Advisory judgment of the exact brief, lesson, and exercises."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    scores: list[QualityAxisScore] = Field(min_length=1)
    findings: list[QualityFinding] = Field(default_factory=list)


class QualityReviewValidation(BaseModel):
    """Deterministic rubric completeness result."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "invalid"]
    total_score: int = Field(ge=0, le=20)
    errors: list[str] = Field(default_factory=list)


PreservationCategory = Literal[
    "dropped_rule",
    "dropped_example",
    "dropped_contrast",
    "dropped_dialogue",
    "dropped_glossary",
    "dropped_intent",
    "changed_meaning",
]


class PreservationFinding(BaseModel):
    """One possible teaching-unit loss reported as an unverified suggestion."""

    model_config = ConfigDict(extra="forbid")

    category: PreservationCategory
    rationale: str = Field(min_length=1)


class NormalizationPreservationReview(BaseModel):
    """Content-preservation judgment of one normalization transform.

    The reviewer never reopens lesson-content authorship: it reports reviewed
    teaching units that did not survive normalization. Mechanical source and
    handle validation runs separately and does not depend on model judgment.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    findings: list[PreservationFinding] = Field(default_factory=list)


__all__ = [
    # Review contracts.
    "LessonQualityReview",
    "NormalizationPreservationReview",
    "PreservationFinding",
    "QualityAxisScore",
    "QualityFinding",
    "QualityReviewValidation",
    # Review vocabulary and model-local policy.
    "PreservationCategory",
    "QualityArtifact",
    "QualityAxis",
    "QualityFindingCode",
    "QualitySeverity",
]
