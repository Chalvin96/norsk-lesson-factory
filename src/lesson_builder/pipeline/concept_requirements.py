"""Entry point: ``load_concept_requirements``.

Validate and load one concept-requirements card as ``ConceptRequirements`` so
cold-authoring and migration code share one strict contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lesson_builder.schema.elements import BloomLevel

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]


def load_concept_requirements(path: Path) -> ConceptRequirements:
    """Read and validate one ``data/concept_requirements/<slug>.json`` card."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ConceptRequirements.model_validate(payload)


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    bloom_targets: list[BloomLevel] = Field(min_length=1)


class ConceptRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1)
    cefr_level: CefrLevel
    objectives: list[Objective] = Field(min_length=1)
    required_anchor_forms: list[str] = Field(default_factory=list)
    notes: str
    min_clean_examples: int | None = None


__all__ = ["ConceptRequirements", "Objective", "load_concept_requirements"]
