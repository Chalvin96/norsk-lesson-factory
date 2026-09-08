"""Not a check itself — typed workflow contracts and scratch results for lesson generation."""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

LessonQualityStatus = Literal[
    "pass",
    "needs_human",
    "content_needs_human",
    "reviewer_unavailable",
    "reviewer_invalid",
    "content_repair_invalid",
    "unconfigured",
    "not_freshly_reviewed",
]


class CheckpointSplit(BaseModel):
    """Learner prose and frozen route-free intent produced from one draft."""

    model_config = ConfigDict(extra="forbid")

    prose_md: str
    intents_yaml: str


class CheckpointIntent(BaseModel):
    """One author-owned evidence decision before operation routing."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1)
    objective_ref: str = Field(min_length=1)
    bloom: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class LessonResult(BaseModel):
    """Result for one catalog owner after generation and package preparation."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    run_id: str
    status: Literal[
        "pending",
        "generated",
        "parked",
        "failed",
        "needs_human_remediation",
    ]
    human_gate: Literal["pending", "not_reached"]
    generated_source_dir: str | None = None
    output_root: str
    slot_plan_hash: str | None = None
    quality_status: LessonQualityStatus | None = None
    coverage_status: str | None = None
    exercise_diagnostics: dict[str, Any] | None = None
    error: str | None = None


class LessonBatchResult(BaseModel):
    """Aggregate result for one bounded parallel scratch generation batch."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["running", "interrupted", "completed"] = "completed"
    batch_id: str
    plan_path: str
    job: str
    max_workers: int = Field(ge=1)
    total: int = Field(ge=0)
    parked: int = Field(ge=0)
    failed: int = Field(ge=0)
    human_gates_pending: int = Field(ge=0)
    quality_pass: int = Field(default=0, ge=0)
    quality_needs_human: int = Field(default=0, ge=0)
    quality_content_needs_human: int = Field(default=0, ge=0)
    quality_reviewer_unavailable: int = Field(default=0, ge=0)
    quality_reviewer_invalid: int = Field(default=0, ge=0)
    quality_content_repair_invalid: int = Field(default=0, ge=0)
    quality_not_freshly_reviewed: int = Field(default=0, ge=0)
    diagnostics_needs_human: int = Field(default=0, ge=0)
    remediation_pending: int = Field(default=0, ge=0)
    results: list[LessonResult]


class LessonPromotionResult(BaseModel):
    """Summary of one explicit promotion from a completed lesson batch."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["promoted", "incomplete", "distribution_failed"] = "promoted"
    batch_id: str
    source_batch_path: str
    source_root: str
    distribution_root: str
    approval_path: str
    approval_paths: list[str] = Field(default_factory=list)
    distribution_status: Literal["assembled", "failed", "not_attempted"] = "assembled"
    total: int = Field(ge=0)
    promoted: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    catalog_ids: list[str]
    source_hashes: dict[str, str]
    export_hashes: dict[str, str]
    outcomes: list[LessonPromotionOutcome] = Field(default_factory=list)


class LessonPromotionOutcome(BaseModel):
    """Durable outcome for one selected lesson promotion attempt."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    approval_path: str | None = None
    approval_id: str | None = None
    status: Literal["promoted", "unchanged", "failed"]
    error: str | None = None


LessonPromotionResult.model_rebuild()


__all__ = [
    "CheckpointIntent",
    "CheckpointSplit",
    "LessonBatchResult",
    "LessonResult",
    "LessonPromotionResult",
    "LessonPromotionOutcome",
    "LessonQualityStatus",
]
