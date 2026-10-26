"""Not a check itself — persisted workbench proposals, chat turns and review reports."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from enum import StrEnum
from typing import Any
from typing import Literal
from typing import TypedDict
from uuid import uuid4

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from lesson_builder.domain.curriculum.models import CurriculumEdit


class ContextReference(BaseModel):
    """Prompt-contained reference, including the exact row key for a selection."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["selection", "finding", "file"]
    label: str
    detail: str = ""
    key: str | None = None
    reference_id: str | None = None


class CurriculumBaseline(BaseModel):
    """Slot values captured before the first local curriculum save for stale plan chips."""

    slots: dict[str, str] = Field(default_factory=dict)


class StoredChatTurn(BaseModel):
    """One persisted chat message or an author turn with live lifecycle state."""

    role: Literal["author", "assistant"]
    text: str
    chips: list[ContextReference] = Field(default_factory=list)
    tool_note: str | None = None
    proposed_edit: CurriculumEdit | None = None
    proposal_id: str | None = None
    turn_id: str | None = None
    codex_turn_id: str | None = None
    status: Literal["pending", "running", "completed", "failed", "cancelled"] | None = None
    partial: str = ""
    error: str | None = None
    kind: Literal["lesson", "curriculum"] = "lesson"
    origin: Literal["chat", "review_fix"] = "chat"
    mode: Literal["chat", "explain_only"] = "chat"
    block_id: str | None = None
    block_hash: str | None = None


class NorwegianStringExplanation(BaseModel):
    """One source Norwegian string and its English rendering for author review."""

    source: str
    english: str


class BlockExplanation(BaseModel):
    """English-only explanation cached for one exact authored block revision."""

    block_id: str
    block_hash: str
    translations: list[NorwegianStringExplanation] = Field(default_factory=list)
    item_tested: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ChatTranscript(BaseModel):
    """Persisted chat messages, Codex thread identity and the last source snapshot."""

    turns: list[StoredChatTurn] = Field(default_factory=list)
    thread_id: str | None = None
    block_snapshot: dict[str, str] = Field(default_factory=dict)
    explanations: dict[str, BlockExplanation] = Field(default_factory=dict)


class ExerciseTranslation(BaseModel):
    """English gloss for one changed exercise string; an English string repeats itself."""

    model_config = ConfigDict(extra="forbid")
    norwegian: str
    english: str


class ExerciseSectionChange(BaseModel):
    """Exact approved and current section text behind one exercise decision."""

    model_config = ConfigDict(extra="forbid")
    section_id: str
    before_text: str | None
    after_text: str | None
    summary: str


class ExerciseAlignmentPinChange(TypedDict):
    """Checkpoint-safe visible change summary for one captured section."""

    section_id: str
    before_text: str | None
    after_text: str | None
    summary: str


class ExerciseAlignmentPin(TypedDict):
    """JSON-native captured alignment evidence for one regeneration exercise."""

    exercise_key: str
    checked_against: dict[str, str]
    section_sources: dict[str, str]
    changed_sections: list[ExerciseAlignmentPinChange]
    current_exercise_source: str


class BlockExplanationResponse(BaseModel):
    """Codex output for translating every Norwegian string and identifying an exercise's target skill."""

    model_config = ConfigDict(extra="forbid")
    translations: list[NorwegianStringExplanation] = Field(default_factory=list)
    item_tested: str


class ResolutionReason(StrEnum):
    """Owner's reason for resolving one review issue."""

    INTENDED = "intended"
    NOT_APPLICABLE = "not_applicable"
    FIXED_ELSEWHERE = "fixed_elsewhere"
    REVIEWER_WRONG = "reviewer_wrong"


class KeepReason(StrEnum):
    """Owner's reason for retaining an exercise after a section change."""

    STILL_MATCHES = "still_matches"
    UNAFFECTED = "unaffected"
    REVISE_LATER = "revise_later"


class ExerciseAlignmentRecord(BaseModel):
    """A local exercise decision or canonical comparison against section versions."""

    model_config = ConfigDict(extra="forbid")
    exercise_key: str
    checked_against: dict[str, str]
    section_sources: dict[str, str] = Field(default_factory=dict)
    outcome: Literal["canonical", "regenerated", "kept"]
    canonical_sha256: str | None = None
    draft_created_at: str | None = None
    note: str | None = None
    reason: KeepReason | None = None
    comment: str | None = None
    at: str
    decision_id: str | None = None

    @model_validator(mode="after")
    def migrate_note(self) -> ExerciseAlignmentRecord:
        """Read old keep notes as comments without inventing an owner reason."""
        if self.comment is None and self.note:
            self.comment = self.note
        return self


class ExerciseAlignmentLog(BaseModel):
    """Local history of explicit alignment decisions for one lesson."""

    records: list[ExerciseAlignmentRecord] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """A grounded AI finding and the author's optional resolution."""

    finding_id: str
    key: str
    criterion: str
    excerpt: str
    explanation: str
    severity: Literal["blocking", "major", "minor"]
    status: Literal["open", "resolved"] = "open"
    resolution_note: str = ""
    resolution_reason: ResolutionReason | None = None
    resolution_comment: str | None = None
    previously_resolved: bool = False
    created_against_sha256: str = ""
    reviewer_version: str = ""
    cited_excerpt: str | None = None
    citation_exact: bool = False
    advisory: bool = False
    reviewer: Literal["primary", "second_opinion"] = "primary"
    block_hash: str = ""
    quote_english: str = ""
    suggested_change: str = ""
    details: str = ""
    message: str = ""

    @model_validator(mode="after")
    def migrate_resolution_note(self) -> ReviewFinding:
        """Read historical free-text decisions as optional comments."""
        if self.resolution_comment is None and self.resolution_note:
            self.resolution_comment = self.resolution_note
        return self


class ReviewRun(BaseModel):
    """Findings bound to source, upstream plan, scope and reviewer policy."""

    digest: str
    plan_sha256: str
    reviewer_version: str
    scope: Literal["lesson", "section", "item"]
    key: str | None
    created_at: str
    findings: list[ReviewFinding]
    summary: str
    job: str = "workbench_reviewer"
    model: str | None = None
    exercise_model: str | None = None
    prompt_version: str | None = None
    criteria_version: str | None = None
    second_opinion_model: str | None = None
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    block_hashes: dict[str, str] = Field(default_factory=dict)


class ReviewCoverage(BaseModel):
    """One review run's evidence for the exact content hash of a source target."""

    model_config = ConfigDict(extra="forbid")
    stage: Literal["lesson", "exercises"]
    block_id: str
    block_hash: str
    review_run_id: str
    reviewer: str
    at: str


class ReviewCoverageStatus(BaseModel):
    """Current source coverage counts and target identities still needing review."""

    covered: int = 0
    total: int = 0
    uncovered: list[str] = Field(default_factory=list)


class ProposedEdit(BaseModel):
    """One agent replacement for an existing source block."""

    model_config = ConfigDict(extra="forbid")
    key: str
    after_source: str
    note: str
    change_summary: str = ""
    translations: list[ExerciseTranslation] = Field(default_factory=list)
    answer_impact: str = "none"
    uncertainty: str | None = None


class ChatResponse(BaseModel):
    """Schema used for every lesson chat and proposal repair call."""

    model_config = ConfigDict(extra="forbid")
    reply: str
    edits: list[ProposedEdit] = Field(default_factory=list)
    regenerate: str | None = None


class CurriculumResponse(BaseModel):
    """Curriculum chat explains entries and may propose a typed curriculum edit."""

    model_config = ConfigDict(extra="forbid")
    reply: str
    proposed_edit: CurriculumEdit | None = None


class ProposalCheck(BaseModel):
    """Deterministic check of a temporary candidate or accepted draft."""

    status: Literal["passed", "failed"] = "passed"
    errors: list[str] = Field(default_factory=list)


class ProposalBlock(BaseModel):
    """A block decision anchored to its exact captured source."""

    key: str
    before: str
    after: str
    note: str
    change_summary: str = ""
    translations: list[ExerciseTranslation] = Field(default_factory=list)
    answer_impact: str = "none"
    uncertainty: str | None = None
    pin: ExerciseAlignmentPin | None = None
    status: Literal["pending", "accepted", "rejected", "conflicted"] = "pending"
    check: ProposalCheck = Field(default_factory=ProposalCheck)

    @model_validator(mode="before")
    @classmethod
    def _restore_legacy_alignment_pin(cls, value: object) -> object:
        """Convert saved proposal evidence into the checkpoint-safe typed Pin shape."""
        if not isinstance(value, dict) or value.get("pin") is not None:
            return value
        checked_against = value.get("checked_against")
        section_sources = value.get("section_sources")
        changed_sections = value.get("changed_sections")
        if not checked_against and not section_sources and not changed_sections:
            return value
        restored = dict(value)
        restored["pin"] = {
            "exercise_key": value["key"],
            "checked_against": checked_against or {},
            "section_sources": section_sources or {},
            "changed_sections": changed_sections or [],
            "current_exercise_source": value["before"],
        }
        for key in ("checked_against", "section_sources", "changed_sections"):
            restored.pop(key, None)
        return restored


class Proposal(BaseModel):
    """One turn's edits and decisions, retained when superseded or settled."""

    proposal_id: str
    lesson_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: Literal["chat", "review_fix", "regenerate", "canonical"]
    created_against_sha256: str
    note: str
    blocks: list[ProposalBlock]
    status: Literal["open", "superseded", "settled"] = "open"
    check: ProposalCheck = Field(default_factory=ProposalCheck)
    run_id: str | None = None
    candidate_review: ReviewRun | None = None
    canonical_sha256: str | None = None
    retries: list[ProposalCheck] = Field(default_factory=list)


class ProposalLog(BaseModel):
    """All proposals, with at most one open proposal."""

    proposals: list[Proposal] = Field(default_factory=list)


class ReviewLog(BaseModel):
    """Hash-bound findings and review coverage accumulated across scoped runs."""

    runs: list[ReviewRun] = Field(default_factory=list)
    coverage: list[ReviewCoverage] = Field(default_factory=list)


class WorkbenchAudioCheckFinding(BaseModel):
    """One deterministic check result attached to an exact audio fingerprint."""

    model_config = ConfigDict(extra="forbid")
    code: Literal["silent", "length", "cut_off", "invalid_wav"]
    message: str


class WorkbenchAudioClip(BaseModel):
    """A source-bound transcript candidate captured for one confirmation."""

    model_config = ConfigDict(extra="forbid")
    binding: str
    row_key: str
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    audio_id: str
    filename: str
    text: str
    translation: str | None = None
    voice: str
    speaker_id: str | None = None
    speaker_name: str | None = None
    origin: Literal["lesson_reading", "lesson_example", "exercise_target"]
    source: dict[str, Any]
    characters: int = Field(ge=1)
    force_synthesis: bool = False


class WorkbenchAudioPlanRecord(BaseModel):
    """An ignored, single-use plan whose token binds confirmation to captured source."""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[a-f0-9]{32}$")
    lesson_id: str
    scope: Literal["block", "missing_and_stale"]
    selection: Literal["missing", "stale", "missing_and_stale"]
    block_key: str | None = None
    source_digest: str
    candidate_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    clips: list[WorkbenchAudioClip]
    characters_to_synthesize: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    reused_count: int = Field(ge=0)
    monthly_spent_usd: float = Field(ge=0)
    monthly_cap_usd: float = Field(ge=0)
    reservation_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    recovery_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    recovery_reserved_characters: int = Field(default=0, ge=0)
    blocked_reason: str | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    result: dict[str, Any] = Field(default_factory=dict)


class WorkbenchAudioIndexClip(BaseModel):
    """Latest workbench-generated bytes and findings for one stable source binding."""

    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_digest: str
    voice: str
    audio_id: str
    duration_ms: int | None = Field(default=None, ge=0)
    findings: list[WorkbenchAudioCheckFinding] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkbenchAudioIndex(BaseModel):
    """Ignored index used to identify stale source bindings and current checks."""

    clips: dict[str, WorkbenchAudioIndexClip] = Field(default_factory=dict)


class WorkbenchAudioSpendLedger(BaseModel):
    """Monthly successful character usage and active reservations for workbench TTS."""

    model_config = ConfigDict(extra="forbid")
    month: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m"), pattern=r"^\d{4}-\d{2}$")
    synthesized_characters: int = Field(default=0, ge=0)
    reservations: dict[str, int] = Field(default_factory=dict)


class WorkbenchAudioReleaseVoices(BaseModel):
    """Speaker voice assignment from the most recently completed standard release."""

    model_config = ConfigDict(extra="forbid")
    tag: str = ""
    lessons: dict[str, dict[str, str]] = Field(default_factory=dict)
