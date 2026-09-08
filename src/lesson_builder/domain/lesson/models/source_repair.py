"""Not a check itself — source-repair result contracts."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit


class MechanicalRepair(BaseModel):
    """One semantics-preserving source normalization applied to a copy."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    location: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class ExerciseSourceRepair(BaseModel):
    """Repair output and the audit evidence that remains after repair."""

    model_config = ConfigDict(extra="forbid")

    exercises_yaml: str
    repairs: list[MechanicalRepair] = Field(default_factory=list)
    audit: MechanicalAudit

    @property
    def changed(self) -> bool:
        """Return whether at least one safe repair was applied."""
        return bool(self.repairs)


__all__ = ["ExerciseSourceRepair", "MechanicalRepair"]
