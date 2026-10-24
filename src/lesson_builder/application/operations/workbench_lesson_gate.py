"""Entry point: `approval` and `promotion` evaluate shared gates; `stage_source_keys` selects approval evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import StageApprovalStatus
from lesson_builder.application.operations.workbench_review import ReviewLedger

LessonGateStage = Literal["plan", "lesson", "exercises"]


def approval(source: LessonGateSource) -> LessonGateDecision:
    """Return the approval blockers and whether current source can be owner-approved."""
    required_stages = tuple(stage for stage in source.stages if stage.status in ("needs_approval", "stale"))
    blockers: list[LessonGateBlocker] = []
    for stage in source.stages:
        blockers.extend(_approval_stage_blockers(source, stage))
    if source.blocking_errors and not any(isinstance(blocker, CheckErrorsBlocker) for blocker in blockers):
        blockers.append(CheckErrorsBlocker())
    return LessonGateDecision(
        blockers=tuple(blockers),
        stages=source.stages,
        required_stages=required_stages,
        allowed=source.workbench_source.exists and bool(required_stages) and not blockers,
    )


def promotion(source: LessonGateSource) -> LessonGateDecision:
    """Return the promotion blockers and whether this exact source can be promoted."""
    approval_decision = approval(source)
    blockers = list(approval_decision.blockers)
    if not source.workbench_source.exists:
        blockers.append(NoDraftBlocker())
    if source.blocking_errors and not any(isinstance(blocker, CheckErrorsBlocker) for blocker in blockers):
        blockers.append(CheckErrorsBlocker())
    if source.audio_blocked:
        blockers.append(AudioBlocker(missing=source.audio_missing, stale=source.audio_stale))
    if source.canonical_changed:
        blockers.append(CanonicalChangedBlocker())
    return LessonGateDecision(
        blockers=tuple(blockers),
        stages=source.stages,
        required_stages=approval_decision.required_stages,
        allowed=(
            source.workbench_source.exists
            and all(stage.status in ("approved", "baseline") for stage in source.stages)
            and not blockers
        ),
    )


def stage_source_keys(source: WorkbenchSource, stage: LessonGateStage) -> set[str]:
    """Return file and block keys owned by one approval stage."""
    filenames = {"plan": "plan.md", "lesson": "lesson.md", "exercises": "exercises.yaml"}
    keys = {filenames[stage]}
    if stage != "plan":
        keys.update(block.key for block in source.blocks if (block.kind == "exercise") == (stage == "exercises"))
    return keys


@dataclass(frozen=True)
class LessonGateSource:
    """Captured facts shared by approval and promotion projections and actions."""

    workbench_source: WorkbenchSource
    stages: tuple[StageApprovalStatus, ...]
    review_ledger: ReviewLedger
    proposal: Proposal | None
    blocking_errors: bool
    audio_blocked: bool = False
    audio_missing: int = 0
    audio_stale: int = 0
    canonical_changed: bool = False


@dataclass(frozen=True)
class LessonGateDecision:
    """One gate result with typed reasons and the exact stage statuses it evaluated."""

    blockers: tuple[LessonGateBlocker, ...]
    stages: tuple[StageApprovalStatus, ...]
    required_stages: tuple[StageApprovalStatus, ...]
    allowed: bool


@dataclass(frozen=True)
class CheckErrorsBlocker:
    """The deterministic source check has blocking errors."""

    kind: Literal["check_errors"] = "check_errors"
    stage: LessonGateStage | None = None
    target: Literal["findings"] = "findings"


@dataclass(frozen=True)
class OpenProposalBlocker:
    """An unsettled proposal changes a source stage awaiting approval."""

    stage: LessonGateStage
    kind: Literal["open_proposal"] = "open_proposal"
    target: Literal["proposal"] = "proposal"


@dataclass(frozen=True)
class ReviewCoverageBlocker:
    """Current source blocks still need review coverage for one stage."""

    stage: Literal["lesson", "exercises"]
    count: int
    uncovered: tuple[str, ...]
    kind: Literal["review_coverage"] = "review_coverage"
    target: Literal["review"] = "review"


@dataclass(frozen=True)
class AudioBlocker:
    """Promotion needs complete current audio with no open audio checks."""

    missing: int
    stale: int
    kind: Literal["audio"] = "audio"
    stage: Literal["lesson"] = "lesson"
    target: Literal["audio"] = "audio"


@dataclass(frozen=True)
class CanonicalChangedBlocker:
    """Canonical source no longer matches the starting package for this draft."""

    kind: Literal["canonical_changed"] = "canonical_changed"
    stage: Literal["lesson"] = "lesson"
    target: Literal["canonical"] = "canonical"


@dataclass(frozen=True)
class NoDraftBlocker:
    """Promotion requires an editable draft package."""

    kind: Literal["no_draft"] = "no_draft"
    stage: None = None
    target: Literal["draft"] = "draft"


@dataclass(frozen=True)
class UnsavedChangesBlocker:
    """Client-side text still needs to be saved before approval."""

    kind: Literal["unsaved_changes"] = "unsaved_changes"
    stage: None = None
    target: Literal["editor"] = "editor"


LessonGateBlocker = (
    CheckErrorsBlocker
    | OpenProposalBlocker
    | ReviewCoverageBlocker
    | AudioBlocker
    | CanonicalChangedBlocker
    | NoDraftBlocker
    | UnsavedChangesBlocker
)


def _approval_stage_is_gated(source: LessonGateSource, stage: StageApprovalStatus) -> bool:
    return stage.status in ("needs_approval", "stale") or (
        not source.workbench_source.exists and stage.status == "baseline" and stage.stage in ("lesson", "exercises")
    )


def _approval_stage_blockers(source: LessonGateSource, stage: StageApprovalStatus) -> tuple[LessonGateBlocker, ...]:
    if not _approval_stage_is_gated(source, stage):
        return ()
    blockers: list[LessonGateBlocker] = []
    if source.blocking_errors:
        blockers.append(CheckErrorsBlocker(stage=stage.stage))
    if source.proposal is not None and _proposal_touches_stage(source, stage.stage):
        blockers.append(OpenProposalBlocker(stage=stage.stage))
    if stage.stage in ("lesson", "exercises"):
        coverage = source.review_ledger.coverage(stage.stage)
        if coverage.total and coverage.uncovered:
            blockers.append(
                ReviewCoverageBlocker(
                    stage=stage.stage,
                    count=len(coverage.uncovered),
                    uncovered=tuple(coverage.uncovered),
                )
            )
    return tuple(blockers)


def _proposal_touches_stage(source: LessonGateSource, stage: LessonGateStage) -> bool:
    if source.proposal is None:
        return False
    keys = stage_source_keys(source.workbench_source, stage)
    return any(block.key in keys and block.status in ("pending", "conflicted") for block in source.proposal.blocks)
