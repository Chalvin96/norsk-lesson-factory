"""Not a check itself — typed contracts returned by lesson review checks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field

ObjectiveAlignmentSeverity = Literal["P1", "P2", "P3"]
NaturalnessSeverity = Literal["P1", "P2", "P3"]
PedagogySeverity = Literal["P0", "P1", "P2", "P3"]
PedagogyCategory = Literal[
    "on_concept",
    "complete",
    "bokmal",
    "sequencing",
    "presentable",
    "answerable",
    "depth",
    "other",
]
K_NATURALNESS_AXIS_MAX = 5
K_PEDAGOGY_AXIS_MAX = 5


class ObjectiveAlignmentIssue(BaseModel):
    objective_id: str
    severity: ObjectiveAlignmentSeverity
    message: str
    evidence: str
    fix: str


class ObjectiveAlignmentReview(BaseModel):
    passed: bool
    summary: str
    issues: list[ObjectiveAlignmentIssue]


class NaturalnessScores(BaseModel):
    idiomatic_phrasing: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)
    register_appropriateness: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)
    terminology_consistency: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)


class NaturalnessIssue(BaseModel):
    unit_id: str
    severity: NaturalnessSeverity
    message: str


class NaturalnessReview(BaseModel):
    scores: NaturalnessScores
    issues: list[NaturalnessIssue]


class PedagogyScores(BaseModel):
    on_concept: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    complete: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    bokmal: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    sequencing: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    presentable: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    answerable: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    depth: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)


class PedagogyIssue(BaseModel):
    severity: PedagogySeverity
    category: PedagogyCategory
    title: str
    evidence: str
    fix: str


class PedagogyReview(BaseModel):
    scores: PedagogyScores
    summary: str
    passes: list[str]
    issues: list[PedagogyIssue]


__all__ = [
    "K_NATURALNESS_AXIS_MAX",
    "K_PEDAGOGY_AXIS_MAX",
    "NaturalnessIssue",
    "NaturalnessReview",
    "NaturalnessScores",
    "NaturalnessSeverity",
    "ObjectiveAlignmentIssue",
    "ObjectiveAlignmentReview",
    "ObjectiveAlignmentSeverity",
    "PedagogyCategory",
    "PedagogyIssue",
    "PedagogyReview",
    "PedagogyScores",
    "PedagogySeverity",
]
