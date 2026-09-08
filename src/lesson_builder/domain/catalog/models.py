"""Not a check itself — typed contracts for curriculum dependency design."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

DependencyKind = Literal["required", "helpful"]
DependencyAssessmentStatus = Literal["reviewed", "needs_human_review"]
DependencyReviewStatus = Literal["needs_human_review", "ready_for_promotion", "failed"]
DependencySecondOpinionSeverity = Literal["P0", "P1", "P2"]
DependencySecondOpinionRecommendation = Literal[
    "keep",
    "upgrade_to_required",
    "downgrade_to_helpful",
    "reject",
    "add_required",
    "add_helpful",
    "needs_human_review",
]


@dataclass(frozen=True)
class OwnerResolution:
    """Deterministic active/deleted/ambiguous owner identity mapping."""

    active_ids: frozenset[str]
    resolved: dict[str, str]
    deleted: frozenset[str]
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def resolve(self, value: str) -> str | None:
        """Return an active owner ID, or ``None`` for deleted/unknown IDs."""
        normalized = normalize_owner_reference(value)
        if normalized in self.ambiguous:
            return None
        return self.resolved.get(normalized)


class DependencyEdge(BaseModel):
    """One proposed prerequisite edge, pointing from prerequisite to dependent."""

    model_config = ConfigDict(extra="forbid")

    prerequisite_id: str = Field(min_length=1)
    dependent_id: str = Field(min_length=1)
    kind: DependencyKind
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class DependencyAssessment(BaseModel):
    """The explicit dependency assessment for one active catalog owner."""

    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=1)
    required_prerequisites: list[str] = Field(default_factory=list)
    helpful_prerequisites: list[str] = Field(default_factory=list)
    status: DependencyAssessmentStatus = "reviewed"
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class DependencyProposal(BaseModel):
    """Structured response expected from the configured dependency reviewer."""

    model_config = ConfigDict(extra="forbid")

    assessments: list[DependencyAssessment] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)


class DependencyProposalRequest(BaseModel):
    """Compact final-owner input sent to the dependency-design model."""

    model_config = ConfigDict(extra="forbid")

    owners: list[dict[str, object]] = Field(default_factory=list)


class DependencySecondOpinionFinding(BaseModel):
    """One traceable advisory finding from the independent reviewer."""

    model_config = ConfigDict(extra="forbid")

    prerequisite_id: str = Field(min_length=1)
    dependent_id: str = Field(min_length=1)
    current_kind: DependencyKind | None = None
    severity: DependencySecondOpinionSeverity = "P2"
    recommendation: DependencySecondOpinionRecommendation
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)


class DependencySecondOpinion(BaseModel):
    """Structured response expected from the independent second reviewer."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    findings: list[DependencySecondOpinionFinding] = Field(default_factory=list)


class DependencySecondOpinionRequest(BaseModel):
    """Compact primary proposal sent to the second-review model."""

    model_config = ConfigDict(extra="forbid")

    owners: list[dict[str, object]] = Field(default_factory=list)
    assessments: list[DependencyAssessment] = Field(default_factory=list)
    proposed_edges: list[DependencyEdge] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class DependencySecondOpinionRecord(BaseModel):
    """Persisted second-opinion result stored beside the dependency review."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed"]
    job: str = "curriculum_reviewer"
    model: str = ""
    summary: str = ""
    findings: list[DependencySecondOpinionFinding] = Field(default_factory=list)
    error: str | None = None


class DependencyReview(BaseModel):
    """Reviewable scratch dependency design bound to one catalog snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1-catalog-dependency-review"
    run_id: str = Field(min_length=1)
    source_catalog: str = Field(min_length=1)
    source_catalog_hash: str = Field(min_length=1)
    source_snapshot: str = ""
    catalog_schema_version: int = Field(ge=2)
    status: DependencyReviewStatus
    owner_count: int = Field(ge=0)
    reviewed_owner_count: int = Field(ge=0)
    assessments: list[DependencyAssessment] = Field(default_factory=list)
    proposed_edges: list[DependencyEdge] = Field(default_factory=list)
    second_opinion: DependencySecondOpinionRecord | None = None
    unresolved: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    provenance: list[dict[str, object]] = Field(default_factory=list)


class DependencyReviewResult(BaseModel):
    """CLI result for a scratch dependency-design run."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["parked_for_review"] = "parked_for_review"
    run_id: str
    review_path: str
    review: DependencyReview


class DependencyPromotionResult(BaseModel):
    """Receipt for one human-approved dependency graph promotion."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["promoted"] = "promoted"
    review_run: str
    approval_path: str
    catalog_path: str
    catalog_hash: str
    owner_count: int = Field(ge=0)
    required_edge_count: int = Field(ge=0)
    helpful_edge_count: int = Field(ge=0)


def normalize_owner_reference(value: str) -> str:
    """Normalize a source or legacy owner reference to its catalog key."""
    normalized = value.strip()
    if normalized.startswith("existing:"):
        normalized = normalized.removeprefix("existing:")
    for prefix in ("legacy__", "approved__"):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    return normalized


__all__ = [
    "DependencyAssessment",
    "DependencyAssessmentStatus",
    "DependencyEdge",
    "DependencyKind",
    "DependencyProposal",
    "DependencyProposalRequest",
    "DependencyPromotionResult",
    "DependencyReview",
    "DependencyReviewResult",
    "DependencyReviewStatus",
    "DependencySecondOpinion",
    "DependencySecondOpinionFinding",
    "DependencySecondOpinionRecommendation",
    "DependencySecondOpinionRecord",
    "DependencySecondOpinionRequest",
    "DependencySecondOpinionSeverity",
    "OwnerResolution",
    "normalize_owner_reference",
]
