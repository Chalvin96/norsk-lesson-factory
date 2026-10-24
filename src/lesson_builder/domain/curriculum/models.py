"""Not a check itself — typed scratch artifacts emitted by the curriculum planner."""

from __future__ import annotations

from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

CatalogKind = Literal["grammar", "phraseology", "pronunciation", "communicative", "writing"]
CurriculumPlanApproval = Literal["pending_human", "auto_approved", "human_approved"]
CurriculumSequencePlacementStatus = Literal["placed", "moved"]


class CatalogOwnerScope(BaseModel):
    """Human-approved atomic scope evidence copied into a plan slot."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["atomic"] = "atomic"
    learner_decision: str = Field(min_length=1)
    assessment_operation: str = Field(min_length=1)
    review_status: Literal["approved"] = "approved"
    reviewed_by: str = Field(min_length=1)


class CatalogTeachingPoint(BaseModel):
    """One approved teaching point retained in the catalog plan slot.

    The catalog owns stable teaching point IDs, learner-facing statements, and
    any bounded contrast or exclusion. The planner carries exactly the reviewed
    payload so the later catalog-generation explanation and author stages receive
    the same authoritative context.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source_owner: str | None = None
    statement: str = Field(min_length=1)


class CurriculumSequenceEntry(BaseModel):
    """One editorial preference for an A1 opening sequence."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class CurriculumSequence(BaseModel):
    """Human-editable, reviewable curriculum ordering preferences."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    entries: list[CurriculumSequenceEntry] = Field(min_length=1)


class CurriculumSequencePlacement(BaseModel):
    """Review evidence for one preferred lesson's requested and actual position."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str = Field(min_length=1)
    requested_position: int = Field(ge=1)
    old_position: int = Field(ge=1)
    new_position: int = Field(ge=1)
    status: CurriculumSequencePlacementStatus
    delayed_by_required_prerequisites: list[str] = Field(default_factory=list)
    helpful_prerequisites_later: list[str] = Field(default_factory=list)


class CurriculumPlanSlot(BaseModel):
    """One approved catalog entry placed in the provisional course order."""

    model_config = ConfigDict(extra="forbid")

    sequence_index: int = Field(ge=0)
    catalog_id: str = Field(min_length=1)
    catalog_kind: CatalogKind
    title: str = Field(min_length=1)
    learner_outcome: str = Field(min_length=1)
    cefr_level: str = Field(min_length=2)
    cefr_source: Literal["catalog", "planner_default"]
    family_id: str = ""
    cefr_tags: list[str] = Field(default_factory=list)
    teaching_points: list[CatalogTeachingPoint] = Field(default_factory=list)
    terminology_ids: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    helpful_prerequisites: list[str] = Field(default_factory=list)
    provisional_flags: list[str] = Field(default_factory=list)
    owner_scope: CatalogOwnerScope | None = None
    human_gate: Literal["pending"] = "pending"


class CurriculumPlan(BaseModel):
    """Reviewable curriculum plan derived only from approved authoring YAML."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    run_id: str
    source_catalog: str
    source_snapshot: str
    source_catalog_hash: str = ""
    source_sequence: str = ""
    source_sequence_hash: str = ""
    dependency_graph_status: Literal["complete", "legacy"] = "legacy"
    dependency_edge_counts: dict[str, int] = Field(default_factory=dict)
    catalog_approval: Literal["human"] = "human"
    curriculum_approval: CurriculumPlanApproval = "pending_human"
    included_catalog_kinds: list[CatalogKind] = Field(default_factory=list)
    excluded_catalog_kinds: list[CatalogKind] = Field(default_factory=list)
    summary: dict[str, int]
    sequence_report: list[CurriculumSequencePlacement] = Field(default_factory=list)
    slots: list[CurriculumPlanSlot]


class CurriculumEntryFields(BaseModel):
    """Human-authored fields shared by manual curriculum entries and split parts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    catalog_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    learner_outcome: str = Field(min_length=1)


class CurriculumManualEntry(CurriculumEntryFields):
    """A plan entry added without generating a lesson package."""

    cefr_level: Literal["A1", "A2", "B1", "B2"] = "A1"
    prerequisites: list[str] = Field(default_factory=list)
    helpful_prerequisites: list[str] = Field(default_factory=list)


class CurriculumSplitPart(CurriculumEntryFields):
    """One authored part and outcome in a curriculum split."""


class CurriculumReorderEdit(BaseModel):
    """A complete authored-order proposal."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["reorder"]
    lesson_ids: list[str] = Field(min_length=1)


class CurriculumAddEdit(BaseModel):
    """A manual entry insertion with an explicit place in the authored order."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["add"]
    entry: CurriculumManualEntry
    after_lesson_id: str | None = None


class CurriculumRemoveEdit(BaseModel):
    """Remove curriculum entries while keeping lesson packages by default."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["remove"]
    lesson_ids: list[str] = Field(min_length=1)
    delete_never_promoted_drafts: bool = False


class CurriculumMergeEdit(BaseModel):
    """Replace two or more entries with one author-reviewed entry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["merge"]
    lesson_ids: list[str] = Field(min_length=2)
    title: str = Field(min_length=1)
    learner_outcome: str = Field(min_length=1)


class CurriculumSplitEdit(BaseModel):
    """Replace one entry with two or more separately authored outcomes."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["split"]
    lesson_id: str
    parts: list[CurriculumSplitPart] = Field(min_length=2)


class CurriculumUpdateEdit(BaseModel):
    """Edit an entry's outcome, level, and required or helpful prerequisites."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["update"]
    lesson_id: str
    title: str = Field(min_length=1)
    learner_outcome: str = Field(min_length=1)
    cefr_level: Literal["A1", "A2", "B1", "B2"]
    prerequisites: list[str]
    helpful_prerequisites: list[str]


CurriculumEdit = Annotated[
    CurriculumReorderEdit
    | CurriculumAddEdit
    | CurriculumRemoveEdit
    | CurriculumMergeEdit
    | CurriculumSplitEdit
    | CurriculumUpdateEdit,
    Field(discriminator="kind"),
]


class CurriculumAffectedLesson(BaseModel):
    """One lesson package or new entry changed by a curriculum edit."""

    lesson_id: str
    title: str
    effect: Literal["needs_replan", "unlisted", "new_entry", "order_changed"]
    detail: str


class CurriculumMovedOutcome(BaseModel):
    """An existing learner outcome carried into another entry during an edit."""

    from_lesson_id: str
    to_lesson_id: str
    outcome: str


class CurriculumMovedPrerequisite(BaseModel):
    """A required or helpful prerequisite reference changed by an edit."""

    lesson_id: str
    from_lesson_id: str
    to_lesson_id: str
    kind: Literal["required", "helpful"]


class CurriculumDependencyFinding(BaseModel):
    """A dependency issue located on one curriculum entry."""

    finding_id: str
    lesson_id: str
    prerequisite_id: str
    code: Literal["curriculum.prerequisite_order", "curriculum.prerequisite_missing"]
    explanation: str


class CurriculumFindingDecision(BaseModel):
    """The author's explicit decision for one located curriculum finding."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    decision: Literal["accept", "revise"]


class CurriculumEditPreview(BaseModel):
    """The complete staged curriculum and the authoring impact of its edits."""

    review_digest: str
    entries: list[CurriculumPlanSlot]
    affected_lessons: list[CurriculumAffectedLesson]
    moved_outcomes: list[CurriculumMovedOutcome]
    moved_prerequisites: list[CurriculumMovedPrerequisite]
    dependency_findings: list[CurriculumDependencyFinding]


class CurriculumPlanResult(BaseModel):
    """CLI result for a catalog plan run and optional committed projection."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["parked_for_review", "ready_for_generation"] = "parked_for_review"
    run_id: str
    plan_path: str
    plan: CurriculumPlan
    committed_plan_path: str | None = None


__all__ = [
    # Shared catalog values.
    "CatalogKind",
    "CurriculumPlanApproval",
    "CurriculumSequence",
    "CurriculumSequenceEntry",
    "CurriculumSequencePlacement",
    "CurriculumSequencePlacementStatus",
    "CatalogOwnerScope",
    "CurriculumPlan",
    "CurriculumPlanResult",
    "CurriculumPlanSlot",
    "CatalogTeachingPoint",
    # Curriculum edits and previews.
    "CurriculumEdit",
    "CurriculumReorderEdit",
    "CurriculumAddEdit",
    "CurriculumRemoveEdit",
    "CurriculumMergeEdit",
    "CurriculumSplitEdit",
    "CurriculumUpdateEdit",
    "CurriculumManualEntry",
    "CurriculumSplitPart",
    "CurriculumEditPreview",
    "CurriculumFindingDecision",
    "CurriculumAffectedLesson",
    "CurriculumMovedOutcome",
    "CurriculumMovedPrerequisite",
    "CurriculumDependencyFinding",
]
