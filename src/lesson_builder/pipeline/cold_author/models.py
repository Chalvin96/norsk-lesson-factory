"""Not a check itself — shared types for the cold-author package.

Stage result types (``StageOK`` / ``StageFailure``), the metadata model
(``ColdMetadata`` / ``ColdObjective``), and the flow result
(``ColdAuthorResult``) used across the cold-author stages and flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lesson_builder.schema.elements import BloomLevel

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2"]


class ColdObjective(BaseModel):
    """One declared objective with a Python-assigned id."""

    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str = Field(min_length=1)
    bloom_targets: list[BloomLevel] = Field(min_length=1)


class ColdMetadata(BaseModel):
    """Stage 1 output: synthesized lesson metadata + objectives."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    cefr_level: CefrLevel
    objectives: list[ColdObjective] = Field(min_length=1)


@dataclass
class StageOK:
    """A successful stage: the authored payload is in ``payload``."""

    stage: str
    payload: Any


@dataclass
class StageFailure:
    """A failed stage: ``reason`` explains why; earlier output was already preserved."""

    stage: str
    reason: str


StageResult = StageOK | StageFailure


class AssemblyError(Exception):
    """Raised when ``assemble_lesson`` cannot produce a gate-clean Lesson."""


ColdAuthorStatus = Literal["parked_for_review", "needs_human", "refused"]


class ColdAuthorResult(BaseModel):
    """One cold-author-flow execution outcome."""

    status: ColdAuthorStatus
    slug: str
    message: str
    run_id: str | None = None
    draft_path: str | None = None
    graph: dict[str, Any] | None = None
    failed_stage: str | None = None
    stage_reason: str | None = None


__all__ = [
    "AssemblyError",
    "CefrLevel",
    "ColdAuthorResult",
    "ColdAuthorStatus",
    "ColdMetadata",
    "ColdObjective",
    "StageFailure",
    "StageOK",
    "StageResult",
]
