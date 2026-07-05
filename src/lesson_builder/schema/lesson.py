"""The internal Lesson model — full shape with provenance. Export projection lives in export.py."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lesson_builder.schema.elements import BloomLevel, Element


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Objective(_Base):
    id: str
    statement: str
    bloom_targets: list[BloomLevel] = Field(min_length=1)


class PoolCard(_Base):
    uuid: UUID
    exercise_id: str


class Pool(_Base):
    key: str
    objective_id: str
    cards: list[PoolCard]

    @model_validator(mode="after")
    def _key_matches_objective(self) -> Pool:
        if self.key != self.objective_id:
            raise ValueError("Pool.key must equal Pool.objective_id")
        return self


class ReviewPool(_Base):
    pools: list[Pool]


class Lesson(_Base):
    model_config = ConfigDict(extra="forbid")

    key: str
    concept_slug: str
    grounding_mode: Literal["grounded", "fallback_no_wiki"]
    title: str
    cefr_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"]
    goal: str
    objectives: list[Objective] = Field(min_length=1)
    elements: list[Element]
    review_pool: ReviewPool
