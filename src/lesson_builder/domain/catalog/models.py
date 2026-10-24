"""Not a check itself — catalog dependency graph contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

DependencyKind = Literal["required", "helpful"]


class DependencyEdge(BaseModel):
    """One proposed prerequisite edge, pointing from prerequisite to dependent."""

    model_config = ConfigDict(extra="forbid")

    prerequisite_id: str = Field(min_length=1)
    dependent_id: str = Field(min_length=1)
    kind: DependencyKind
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


__all__ = [
    "DependencyEdge",
    "DependencyKind",
]
