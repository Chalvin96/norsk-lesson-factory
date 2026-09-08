"""Not a check itself — source-audit result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

ReviewArtifact = Literal["lesson.md", "exercises.yaml"]
K_SOURCE_AUDIT_LESSON_FILE: ReviewArtifact = "lesson.md"
K_SOURCE_AUDIT_EXERCISES_FILE: ReviewArtifact = "exercises.yaml"


class MechanicalFinding(BaseModel):
    """One deterministic source invariant violation or review warning."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: Literal["blocking", "major", "minor"]
    artifact: ReviewArtifact
    location: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class MechanicalBuiltAnswer(BaseModel):
    """One build target assembled from its authored token order."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1)
    text: str = Field(min_length=1)
    token_ids: list[str] = Field(min_length=1)


class MechanicalAudit(BaseModel):
    """Serializable deterministic evidence for a review/edit pass."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["clean", "warning", "blocked", "invalid"]
    exercise_handles: list[str] = Field(default_factory=list)
    marker_handles: list[str] = Field(default_factory=list)
    built_answers: list[MechanicalBuiltAnswer] = Field(default_factory=list)
    findings: list[MechanicalFinding] = Field(default_factory=list)

    @property
    def material_findings(self) -> list[MechanicalFinding]:
        """Return findings that must be resolved before compile/park."""
        return [finding for finding in self.findings if finding.severity in {"blocking", "major"}]


__all__ = [
    "K_SOURCE_AUDIT_EXERCISES_FILE",
    "K_SOURCE_AUDIT_LESSON_FILE",
    "MechanicalAudit",
    "MechanicalBuiltAnswer",
    "MechanicalFinding",
    "ReviewArtifact",
]
