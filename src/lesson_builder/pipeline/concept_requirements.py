"""Entry point: ``load_concept_requirements``.

Validate and load one concept-requirements card as ``ConceptRequirements`` so
cold-authoring and migration code share one strict contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lesson_builder.schema.elements import BloomLevel

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]

# Canonical placeholder-objective pattern. A raw ``"Learn <slug>"`` — the shape a
# scaffold card carries before a real objective is written. Anchored to the single
# slug token, so real sentences ("Learn common compounds as whole words") never
# match. Lives HERE (the write boundary) so a placeholder card is rejected at
# load/validate time and can never regenerate into a lesson; the lesson-side gate
# (objective_structural.objective_placeholder) imports this same pattern.
K_PLACEHOLDER_OBJECTIVE_RE = re.compile(r"^Learn [a-z0-9_]+$")


def is_placeholder_objective(statement: str) -> bool:
    """True when ``statement`` is a raw ``"Learn <slug>"`` scaffold placeholder."""
    return bool(K_PLACEHOLDER_OBJECTIVE_RE.match(statement))


def load_concept_requirements(path: Path) -> ConceptRequirements:
    """Read and validate one ``data/concept_requirements/<slug>.json`` card."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ConceptRequirements.model_validate(payload)


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    bloom_targets: list[BloomLevel] = Field(min_length=1)

    @field_validator("statement")
    @classmethod
    def _reject_placeholder(cls, value: str) -> str:
        """Write-path guard: a card must not carry a raw ``"Learn <slug>"``
        objective. Rejecting it here (at card load/validate) stops the placeholder
        from ever flowing through cold-authoring into a shipped lesson — the
        producer-level fix for the placeholder-regresses-from-the-card defect."""
        if is_placeholder_objective(value):
            raise ValueError(
                f'objective statement is a raw placeholder ("{value}"); '
                "write a real objective sentence before authoring from this card"
            )
        return value


class ConceptRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1)
    cefr_level: CefrLevel
    objectives: list[Objective] = Field(min_length=1)
    required_anchor_forms: list[str] = Field(default_factory=list)
    notes: str
    min_clean_examples: int | None = None


__all__ = [
    "ConceptRequirements",
    "Objective",
    "load_concept_requirements",
    "is_placeholder_objective",
    "K_PLACEHOLDER_OBJECTIVE_RE",
]
