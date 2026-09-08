"""Not a check itself — the typed result of deterministic exercise diagnostics."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ExerciseDiagnostics(BaseModel):
    """Deterministic build evidence about one authored exercise package."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["observed", "needs_human"]
    total_exercises: int = Field(ge=0)
    response_opportunities: int = Field(ge=0)
    operation_counts: dict[str, int]
    objective_counts: dict[str, int]
    bloom_counts: dict[str, int]
    phase_counts: dict[str, int]
    dominant_operation_share: float = Field(ge=0, le=1)
    binary_option_saturation: float = Field(ge=0, le=1)
    findings: list[str]
    diagnostics: list[str] = Field(default_factory=list)
