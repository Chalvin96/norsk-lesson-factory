"""Not a check itself — quality-review contracts and their local invariants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

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
    "articulation_cue",
    "contrast_to_utterance",
    "speech_act_purpose",
    "turn_choice_consequence",
    "repair_and_transfer",
    "model_text_fitness",
    "organization_language_choices",
    "revision_guidance",
]
QualitySeverity = Literal["blocking", "major", "minor"]
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
K_QUALITY_REVIEW_BLOCKING_CODES: tuple[str, ...] = (
    "incorrect_claim",
    "unsupported_claim",
    "unnatural_bokmal",
    "meaning_changing_translation",
    "accepted_form_marked_wrong",
    "ambiguous_answer_key",
    "target_loss",
    "prerequisite_scope_breach",
    "compiler_failure",
)


class QualityAxisScore(BaseModel):
    """One complete 0–2 rubric judgment tied to supplied evidence."""

    model_config = ConfigDict(extra="forbid")

    axis: QualityAxis
    score: int = Field(ge=0, le=2)
    rationale: str = Field(min_length=1)


class QualityFinding(BaseModel):
    """One exact defect and bounded source repair instruction."""

    model_config = ConfigDict(extra="forbid")

    code: QualityFindingCode
    severity: QualitySeverity
    artifact: QualityArtifact
    location: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    repair_instruction: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_blocking_code_severity(self) -> QualityFinding:
        if self.code in K_QUALITY_REVIEW_BLOCKING_CODES and self.severity != "blocking":
            raise ValueError(f"quality finding {self.code!r} must use blocking severity")
        return self


class LessonQualityReview(BaseModel):
    """Strict independent judgment of the exact brief, lesson, and exercises."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "needs_repair"]
    summary: str = Field(min_length=1)
    scores: list[QualityAxisScore] = Field(min_length=1)
    findings: list[QualityFinding] = Field(default_factory=list)


class QualityReviewValidation(BaseModel):
    """Deterministic rubric completeness and pass-rule result."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "invalid"]
    total_score: int = Field(ge=0, le=20)
    expected_verdict: Literal["pass", "needs_repair"]
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
    """One reviewed teaching unit lost or semantically changed by normalization."""

    model_config = ConfigDict(extra="forbid")

    category: PreservationCategory
    evidence: str = Field(min_length=1)
    repair_instruction: str = Field(min_length=1)


class NormalizationPreservationReview(BaseModel):
    """Content-preservation judgment of one normalization transform.

    The reviewer never reopens lesson-content authorship: it reports reviewed
    teaching units that did not survive normalization. Mechanical source and
    handle validation runs separately and does not depend on model judgment.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "needs_repair"]
    summary: str = Field(min_length=1)
    findings: list[PreservationFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_matches_findings(self) -> NormalizationPreservationReview:
        if self.verdict == "pass" and self.findings:
            raise ValueError("a passing preservation review cannot contain findings")
        if self.verdict == "needs_repair" and not self.findings:
            raise ValueError("needs_repair preservation review must report a finding")
        return self


__all__ = [
    # Review contracts.
    "LessonQualityReview",
    "NormalizationPreservationReview",
    "PreservationFinding",
    "QualityAxisScore",
    "QualityFinding",
    "QualityReviewValidation",
    # Review vocabulary and model-local policy.
    "K_QUALITY_REVIEW_BLOCKING_CODES",
    "PreservationCategory",
    "QualityArtifact",
    "QualityAxis",
    "QualityFindingCode",
    "QualitySeverity",
]
