"""Entry point: `check_promotion` checks draft, source, proposal and audio readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_records import Proposal

LessonGateStage = Literal["plan", "lesson", "exercises"]


def check_promotion(source: LessonGateSource) -> LessonGateDecision:
    """Require valid source, settled proposals, complete audio and no canonical conflict."""
    blockers: list[LessonGateBlocker] = []
    if not source.workbench_source.exists:
        blockers.append(NoDraftBlocker())
    if source.blocking_errors:
        blockers.append(CheckErrorsBlocker())
    if source.proposal is not None and any(
        block.status in ("pending", "conflicted") for block in source.proposal.blocks
    ):
        blockers.append(OpenProposalBlocker())
    if source.audio_blocked:
        blockers.append(AudioBlocker(missing=source.audio_missing, stale=source.audio_stale))
    if source.canonical_changed:
        blockers.append(CanonicalChangedBlocker())
    return LessonGateDecision(blockers=tuple(blockers), allowed=not blockers)


@dataclass(frozen=True)
class LessonGateSource:
    """Captured authoring facts shared by the promotion projection and action."""

    workbench_source: WorkbenchSource
    proposal: Proposal | None
    blocking_errors: bool
    audio_blocked: bool = False
    audio_missing: int = 0
    audio_stale: int = 0
    canonical_changed: bool = False


@dataclass(frozen=True)
class LessonGateDecision:
    """Promotion readiness and the reasons it is blocked."""

    blockers: tuple[LessonGateBlocker, ...]
    allowed: bool


@dataclass(frozen=True)
class CheckErrorsBlocker:
    """The deterministic source check has blocking errors."""

    kind: Literal["check_errors"] = "check_errors"
    stage: LessonGateStage | None = None
    target: Literal["findings"] = "findings"


@dataclass(frozen=True)
class OpenProposalBlocker:
    """An unsettled proposal changes a source being promoted."""

    stage: LessonGateStage | None = None
    kind: Literal["open_proposal"] = "open_proposal"
    target: Literal["proposal"] = "proposal"


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
    """Client-side text still needs to be saved before promotion."""

    kind: Literal["unsaved_changes"] = "unsaved_changes"
    stage: None = None
    target: Literal["editor"] = "editor"


LessonGateBlocker = (
    CheckErrorsBlocker
    | OpenProposalBlocker
    | AudioBlocker
    | CanonicalChangedBlocker
    | NoDraftBlocker
    | UnsavedChangesBlocker
)
