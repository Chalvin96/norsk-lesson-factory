"""Not a check itself — Promptfoo authoring-evaluation data contracts."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class NormalizedPackage(BaseModel):
    """Transport envelope for lesson prose and exercise requests."""

    model_config = ConfigDict(extra="forbid")

    lesson_md: str
    exercise_requests_yaml: str


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


__all__ = [
    "ExercisePackage",
    "ExerciseRequest",
    "NormalizedPackage",
]
