"""Not a check itself — rich-authoring data contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


class NormalizedPackage(BaseModel):
    """Transport envelope for lesson prose and exercise requests."""

    model_config = ConfigDict(extra="forbid")

    lesson_md: str
    exercise_requests_yaml: str


class NormalizationTextEdit(BaseModel):
    """One exact replacement allowed during normalization repair."""

    model_config = ConfigDict(extra="forbid")

    artifact: Literal["lesson_md", "exercise_requests_yaml"]
    finding_ref: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _replacement_must_change_text(self) -> NormalizationTextEdit:
        if self.old_text == self.new_text:
            raise ValueError("normalization edit must change the source text")
        return self


class NormalizationEditResponse(BaseModel):
    """Transport envelope for bounded exact normalization edits."""

    model_config = ConfigDict(extra="forbid")

    edits: list[NormalizationTextEdit]


class ExercisePackage(BaseModel):
    """Transport envelope emitted by the independent exercise-author node."""

    model_config = ConfigDict(extra="forbid")

    exercises_yaml: str


class ExerciseRequest(BaseModel):
    """One compact practice checkpoint before operation details are authored.

    The request carries only the stable identity and the constraints the
    downstream exercise author must honor: the handle, the objective
    reference, the Bloom level, the operation-independent evidence route, and
    one concise observable evidence statement. It never carries learner-facing
    exercise wording, context prose, or success-response narration.
    """

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1)
    objective_ref: str = Field(min_length=1)
    bloom: str = Field(min_length=1)
    evidence_route: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class LessonDraftEdit(BaseModel):
    """One exact replacement allowed during a lesson-draft repair."""

    model_config = ConfigDict(extra="forbid")

    finding_ref: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class LessonDraftEditResponse(BaseModel):
    """Transport envelope for bounded, exact lesson-draft edits."""

    model_config = ConfigDict(extra="forbid")

    edits: list[LessonDraftEdit]


__all__ = [
    "ExercisePackage",
    "ExerciseRequest",
    "LessonDraftEdit",
    "LessonDraftEditResponse",
    "NormalizationEditResponse",
    "NormalizationTextEdit",
    "NormalizedPackage",
]
