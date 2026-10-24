"""Entry point: `build_workbench_lesson_state` projects source-bound workbench pane state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal
from typing import cast

from lesson_builder.application.operations.workbench_approval import derive_stage_statuses
from lesson_builder.application.operations.workbench_approval import find_approved_snapshot
from lesson_builder.application.operations.workbench_audio import project_workbench_audio
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignmentState
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateBlocker
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateSource
from lesson_builder.application.operations.workbench_lesson_gate import approval
from lesson_builder.application.operations.workbench_lesson_gate import promotion
from lesson_builder.application.operations.workbench_promotion import canonical_source_digest
from lesson_builder.application.operations.workbench_proposals import ProposalDecision
from lesson_builder.application.operations.workbench_proposals import allowed_decisions
from lesson_builder.application.operations.workbench_proposals import read_open_proposal
from lesson_builder.application.operations.workbench_records import BlockExplanation
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_records import ExerciseSectionChange
from lesson_builder.application.operations.workbench_records import ExerciseTranslation
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import ResolutionReason
from lesson_builder.application.operations.workbench_records import ReviewCoverageStatus
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_records import StageApprovalStatus
from lesson_builder.application.operations.workbench_records import StoredChatTurn
from lesson_builder.application.operations.workbench_review import ReviewLedger
from lesson_builder.application.operations.workbench_review import block_content_hash
from lesson_builder.application.operations.workbench_review import finding_label
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.formats.workbench_proposal import ProposalText
from lesson_builder.formats.workbench_proposal import render_proposal_text
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record

FindingOrigin = Literal["ai", "check"]
FindingSeverity = Literal["warning", "error"]
FindingStatus = Literal["open", "resolved"]
ChipTone = Literal["neutral", "draft", "warning", "error", "ok"]
ProposalOrigin = Literal["chat", "review_fix", "regenerate", "canonical"]


@dataclass(frozen=True)
class BlockTarget:
    """One lesson element, or one block inside a section, that a finding or proposal points at."""

    element_id: str
    block_index: int | None = None


@dataclass(frozen=True)
class StatusChip:
    """A short derived status label shown on a lesson or stage."""

    label: str
    tone: ChipTone = "neutral"


@dataclass(frozen=True)
class ActiveTurnMetadata:
    """Polling and cancellation identity for one pending authoring turn."""

    turn_id: str
    status: Literal["pending", "running"]
    partial: str


@dataclass(frozen=True)
class WorkbenchFinding:
    """One review or check finding shown as a margin note beside its block."""

    finding_id: str
    target: BlockTarget
    origin: FindingOrigin
    severity: FindingSeverity
    status: FindingStatus
    code: str
    excerpt: str
    explanation: str
    resolution_note: str = ""
    resolution_reason: ResolutionReason | None = None
    resolution_comment: str | None = None
    previously_resolved: bool = False
    level: str = "minor"
    advisory: bool = False
    reviewer: Literal["primary", "second_opinion"] = "primary"
    label: str = "Finding"
    message: str = ""
    quote_english: str = ""
    suggested_change: str = ""
    details: str = ""


@dataclass(frozen=True)
class WorkbenchProposal:
    """One pending chat proposal rendered as a Before / After card under its block."""

    proposal_id: str
    target: BlockTarget
    before: str
    after: str
    note: str
    origin: ProposalOrigin
    key: str = ""
    before_rendered: tuple[ProposalText, ...] = ()
    after_rendered: tuple[ProposalText, ...] = ()
    status: Literal["pending", "accepted", "rejected", "conflicted"] = "pending"
    check: ProposalCheck = field(default_factory=ProposalCheck)
    change_summary: str = ""
    translations: tuple[ExerciseTranslation, ...] = ()
    answer_impact: str = "none"
    uncertainty: str | None = None
    changed_sections: tuple[ExerciseSectionChange, ...] = ()
    allowed_decisions: tuple[ProposalDecision, ...] = ()


@dataclass(frozen=True)
class WorkbenchLessonState:
    """Review, proposal, chat, approval and stale exercise state for one open lesson."""

    findings: tuple[WorkbenchFinding, ...] = ()
    proposals: tuple[WorkbenchProposal, ...] = ()
    chat: tuple[StoredChatTurn, ...] = ()
    active_turn: ActiveTurnMetadata | None = None
    status_chips: tuple[StatusChip, ...] = ()
    approve_blockers: tuple[LessonGateBlocker, ...] = ()
    approval_stages: tuple[StageApprovalStatus, ...] = ()
    approvable: bool = False
    exercise_staleness: tuple[ExerciseAlignmentState, ...] = ()
    promotable: bool = False
    promotion_blockers: tuple[LessonGateBlocker, ...] = ()
    proposal_check: ProposalCheck | None = None
    review_summary: str | None = None
    review_created_at: str | None = None
    review_digest: str | None = None
    review_coverage: ReviewCoverageStatus = field(default_factory=ReviewCoverageStatus)
    check_finding_count: int = 0
    ai_finding_count: int = 0
    explanations: dict[str, BlockExplanation] = field(default_factory=dict)
    approval_changes: dict[str, WorkbenchProposal] = field(default_factory=dict)


def build_workbench_lesson_state(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    check_findings: tuple[WorkbenchFinding, ...],
    blocking_errors: bool,
    *,
    plan: CurriculumPlan | None = None,
    unmatched_check_count: int = 0,
    audio_projection: dict[str, object] | None = None,
) -> WorkbenchLessonState:
    """Derive pane state and approval gates from current authoring facts."""
    reviews = read_workbench_record(root, "reviews", lesson_id, ReviewLog)
    ledger = ReviewLedger(source, reviews)
    transcript = read_workbench_record(root, "chat", lesson_id, ChatTranscript)
    proposal = read_open_proposal(root, lesson_id, source)
    latest = next(reversed(reviews.runs), None)
    coverage = ledger.coverage()
    current_hashes = {block.key: block_content_hash(block.source) for block in source.blocks}
    explanations = {
        item.block_id: item
        for item in transcript.explanations.values()
        if current_hashes.get(item.block_id) == item.block_hash
    }
    findings = check_findings + tuple(
        WorkbenchFinding(
            finding_id=item.finding_id,
            target=block_target(item.key),
            origin="ai",
            severity="warning",
            status=item.status,
            code=item.criterion,
            excerpt=item.excerpt,
            explanation=item.explanation,
            resolution_note=item.resolution_note,
            resolution_reason=item.resolution_reason,
            resolution_comment=item.resolution_comment,
            previously_resolved=item.previously_resolved,
            level=item.severity,
            advisory=True,
            reviewer=item.reviewer,
            label=finding_label(item.criterion),
            message=item.message or item.explanation,
            quote_english=item.quote_english,
            suggested_change=item.suggested_change,
            details=item.details
            or json.dumps({"criterion": item.criterion, "explanation": item.explanation}, ensure_ascii=False),
        )
        for item in ledger.current_findings()
    )
    exercise_staleness = ExerciseAlignment(root, lesson_id).states(source)
    check_count = sum(item.status == "open" and item.origin == "check" for item in findings) + unmatched_check_count
    ai_count = sum(item.status == "open" and item.origin == "ai" for item in findings)
    approvals = derive_stage_statuses(root, lesson_id, source, plan=plan)
    audio = audio_projection or project_workbench_audio(root, lesson_id)
    gate_source = LessonGateSource(
        workbench_source=source,
        stages=approvals,
        review_ledger=ledger,
        proposal=proposal,
        blocking_errors=blocking_errors,
        audio_blocked=bool(audio["blockers"]),
        audio_missing=cast(int, audio["missing_count"]),
        audio_stale=cast(int, audio["stale_count"]),
        canonical_changed=source.exists and canonical_source_digest(root, lesson_id) != source.started_from,
    )
    approval_gate = approval(gate_source)
    promotion_gate = promotion(gate_source)
    approvals = approval_gate.stages
    snapshot_unavailable = _has_unavailable_approved_snapshot(root, lesson_id)
    chips = _build_status_chips(source, check_count, ai_count, approvals, snapshot_unavailable, proposal is not None)
    return WorkbenchLessonState(
        findings=findings,
        proposals=tuple(
            WorkbenchProposal(
                proposal_id=proposal.proposal_id if proposal else "",
                target=block_target(block.key),
                before=block.before,
                after=block.after,
                note=block.note,
                origin=proposal.source if proposal else "chat",
                key=block.key,
                before_rendered=render_proposal_text(
                    block.before,
                    kind="exercise"
                    if block.key == "exercises.yaml"
                    or any(item.key == block.key and item.kind == "exercise" for item in source.blocks)
                    else "markdown",
                ),
                after_rendered=render_proposal_text(
                    block.after,
                    kind="exercise"
                    if block.key == "exercises.yaml"
                    or any(item.key == block.key and item.kind == "exercise" for item in source.blocks)
                    else "markdown",
                ),
                status=block.status,
                check=block.check,
                change_summary=block.change_summary,
                translations=tuple(block.translations),
                answer_impact=block.answer_impact,
                uncertainty=block.uncertainty,
                changed_sections=(
                    tuple(ExerciseSectionChange.model_validate(item) for item in block.pin["changed_sections"])
                    if block.pin is not None
                    else ()
                ),
                allowed_decisions=allowed_decisions(proposal, block, source) if proposal is not None else (),
            )
            for block in (proposal.blocks if proposal else [])
        ),
        chat=tuple(turn for turn in transcript.turns if turn.mode == "chat"),
        active_turn=_find_active_turn_metadata(transcript.turns),
        status_chips=tuple(chips),
        approve_blockers=approval_gate.blockers,
        approval_stages=approvals,
        approvable=approval_gate.allowed,
        exercise_staleness=exercise_staleness,
        promotable=promotion_gate.allowed,
        promotion_blockers=promotion_gate.blockers,
        proposal_check=proposal.check if proposal else None,
        review_summary=latest.summary if latest else None,
        review_created_at=latest.created_at if latest else None,
        review_digest=latest.digest if latest else None,
        review_coverage=coverage,
        check_finding_count=check_count,
        ai_finding_count=ai_count,
        explanations=explanations,
        approval_changes=_approval_changes(root, lesson_id, source, approvals),
    )


def build_curriculum_lesson_status_chips(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    *,
    plan: CurriculumPlan | None = None,
) -> tuple[StatusChip, ...]:
    """Project only the status chips used by one curriculum lesson row."""
    reviews = read_workbench_record(root, "reviews", lesson_id, ReviewLog)
    ai_count = sum(finding.status == "open" for finding in ReviewLedger(source, reviews).current_findings())
    proposals = read_workbench_record(root, "proposals", lesson_id, ProposalLog)
    approvals = derive_stage_statuses(root, lesson_id, source, plan=plan)
    chips = _build_status_chips(
        source,
        None,
        ai_count,
        approvals,
        _has_unavailable_approved_snapshot(root, lesson_id),
        any(item.status == "open" for item in proposals.proposals),
    )
    return tuple(chips)


def block_target(key: str) -> BlockTarget:
    """Convert a source row key into the existing rendered row target."""
    element, separator, index = key.partition("#")
    return BlockTarget(element, int(index) if separator and index.isdigit() else None)


def _find_active_turn_metadata(turns: list[StoredChatTurn]) -> ActiveTurnMetadata | None:
    for turn in reversed(turns):
        if turn.role == "author" and turn.turn_id and turn.status in ("pending", "running"):
            return ActiveTurnMetadata(turn_id=turn.turn_id, status=turn.status, partial=turn.partial)
    return None


def _build_status_chips(
    source: WorkbenchSource,
    check_count: int | None,
    ai_count: int,
    approvals: tuple[StageApprovalStatus, ...],
    snapshot_unavailable: bool,
    has_open_proposal: bool,
) -> list[StatusChip]:
    """Build row chips; a None check count leaves checks to the lazily loaded curriculum count."""
    findings_chip = (
        StatusChip(f"{ai_count} AI", "neutral")
        if check_count is None
        else StatusChip(
            f"{check_count} {'check' if check_count == 1 else 'checks'} · {ai_count} AI",
            "warning" if check_count else "neutral",
        )
    )
    chips = [
        StatusChip("Draft" if source.exists else "Canonical", "draft" if source.exists else "neutral"),
        findings_chip,
    ]
    chips.extend(_stage_status_chips(source, approvals))
    if snapshot_unavailable:
        chips.append(StatusChip("approved snapshot unavailable", "error"))
    if has_open_proposal:
        chips.append(StatusChip("Open proposal", "draft"))
    return chips


def _approval_changes(
    root: Path, lesson_id: str, source: WorkbenchSource, approvals: tuple[StageApprovalStatus, ...]
) -> dict[str, WorkbenchProposal]:
    changes: dict[str, WorkbenchProposal] = {}
    paths = WorkspacePaths(root)
    for stage in approvals:
        if stage.status != "stale" or stage.stage == "plan":
            continue
        key = "plan.md" if stage.stage == "lesson" else "lesson.md"
        canonical = paths.lessons_root / lesson_id / key
        before = canonical.read_text() if canonical.is_file() else ""
        after = source.files[key]
        changes[stage.stage] = WorkbenchProposal(
            proposal_id=f"upstream-{stage.stage}",
            target=BlockTarget(key),
            key=key,
            before=before,
            after=after,
            note="Upstream source changed since the last approval.",
            origin="canonical",
            before_rendered=render_proposal_text(before),
            after_rendered=render_proposal_text(after),
        )
    return changes


def _stage_status_chips(source: WorkbenchSource, approvals: tuple[StageApprovalStatus, ...]) -> list[StatusChip]:
    chips: list[StatusChip] = []
    for item in approvals:
        if item.status in ("needs_approval", "stale"):
            chips.append(
                StatusChip(
                    f"{'needs approval' if item.status == 'needs_approval' else 'stale'}: {item.stage}", "warning"
                )
            )
    if all(item.status in ("approved", "baseline") for item in approvals):
        chips.append(
            StatusChip("baseline" if any(item.status == "baseline" for item in approvals) else "owner accepted", "ok")
        )
    if not source.exists and any(item.status in ("needs_approval", "stale") for item in approvals):
        chips.append(StatusChip("out of sync", "error"))
    return chips


def _has_unavailable_approved_snapshot(root: Path, lesson_id: str) -> bool:
    """Check human snapshots that workbench freshness depends on."""
    return (
        find_approved_snapshot(root, lesson_id, "lesson").unavailable
        or find_approved_snapshot(root, lesson_id, "exercises").unavailable
    )


__all__ = [
    # Targets and chips
    "ActiveTurnMetadata",
    "BlockTarget",
    "StatusChip",
    # Lesson tab
    "WorkbenchFinding",
    "WorkbenchLessonState",
    "WorkbenchProposal",
]
