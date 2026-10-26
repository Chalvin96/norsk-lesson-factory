"""Not a check itself — TypedDict wrappers name workbench HTTP payload shapes."""

from __future__ import annotations

from typing import Literal
from typing import NotRequired
from typing import Required
from typing import TypedDict

from lesson_builder.application.operations.workbench_audio import WorkbenchAudioPlan
from lesson_builder.application.operations.workbench_curriculum import CurriculumFinding
from lesson_builder.application.operations.workbench_lessons import WorkbenchLesson
from lesson_builder.application.operations.workbench_new_lesson import NewLessonRequest
from lesson_builder.application.operations.workbench_records import ContextReference
from lesson_builder.application.operations.workbench_records import KeepReason
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ResolutionReason
from lesson_builder.application.operations.workbench_records import StoredChatTurn
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettingsResponse
from lesson_builder.application.operations.workbench_state import ChipTone
from lesson_builder.application.operations.workbench_state import WorkbenchFinding
from lesson_builder.application.operations.workbench_state import WorkbenchLessonState
from lesson_builder.domain.curriculum.models import CurriculumEdit
from lesson_builder.domain.curriculum.models import CurriculumEditPreview
from lesson_builder.workflow.workbench_regeneration.state import RegenerationResponse


class WorkbenchSourceBlock(TypedDict):
    """One editable source span in the workbench API."""

    key: str
    kind: str
    source: str
    fields: dict[str, str]


class WorkbenchDraftStatus(TypedDict):
    """Git and draft identity shown by the lesson editor."""

    exists: bool
    startedFrom: str | None
    dirty: bool
    lastCommit: str | None


class WorkbenchPlanSource(TypedDict):
    """Parsed plan source with its exact-source recovery result."""

    frontmatter: dict[str, object]
    body: str
    parseError: str | None


class WorkbenchLessonHeader(TypedDict):
    """Lesson-level fields rendered above the authored packet."""

    title: str | None
    cefrLevel: str | None
    kind: str | None
    goal: str | None


class WorkbenchSourceFinding(TypedDict, total=False):
    """One deterministic finding returned by source compilation."""

    code: str
    severity: str
    artifact: str
    location: str
    explanation: str
    evidence: str


class WorkbenchStatusChip(TypedDict):
    """One status label in the curriculum list."""

    label: str
    tone: ChipTone


class WorkbenchCurriculumEntry(TypedDict):
    """One curriculum row's authored outcome and current status projection."""

    learnerOutcome: str
    cefrLevel: str
    findings: list[CurriculumFinding]
    statusChips: list[WorkbenchStatusChip]
    dependsOn: list[str]
    helpfulPrerequisites: list[str]


class WorkbenchCurriculumPayload(TypedDict):
    """Curriculum response consumed by the workbench SPA."""

    lessons: list[WorkbenchLesson]
    entries: dict[str, WorkbenchCurriculumEntry]
    chat: list[StoredChatTurn]
    findings: list[CurriculumFinding]


class WorkbenchCurriculumChecks(TypedDict):
    """Deterministic check counts per curriculum lesson, loaded after the curriculum rows."""

    counts: dict[str, int]


class WorkbenchSourcePayload(TypedDict):
    """Exact editor files and draft state returned by the source route."""

    files: dict[str, str]
    blocks: list[WorkbenchSourceBlock]
    draft: WorkbenchDraftStatus


class WorkbenchAudioFinding(TypedDict):
    """One author-facing check attached to an audio clip."""

    code: Literal["silent", "length", "cut_off", "invalid_wav"]
    message: str


class WorkbenchAudioClipStatus(TypedDict):
    """Current audio readiness for one authored block."""

    binding: str
    rowKey: str
    fingerprint: str
    audioId: str
    filename: str
    text: str
    translation: str | None
    voice: str
    speakerId: str | None
    speakerName: str | None
    origin: Literal["lesson_reading", "lesson_example", "exercise_target"]
    characters: int
    status: Literal["ready", "missing", "stale"]
    durationMs: int | None
    playUrl: str
    findings: list[WorkbenchAudioFinding]


class WorkbenchAudioVoice(TypedDict):
    """Voice assignment compared with the latest standard release."""

    speakerId: str
    speakerName: str
    voice: str
    changedSinceRelease: bool
    releasedVoice: str | None


class WorkbenchAudioStatus(TypedDict):
    """Current clip readiness and author-only audio blockers."""

    clips: list[WorkbenchAudioClipStatus]
    voices: list[WorkbenchAudioVoice]
    missingCount: int
    staleCount: int
    readyCount: int
    blockers: list[str]


class WorkbenchLessonPayload(TypedDict):
    """Compiled lesson and source state consumed by the lesson pane."""

    lessonId: str
    source: Literal["draft", "canonical"]
    sourceLabel: str
    lessonSource: str
    planSource: str
    planPreview: WorkbenchPlanSource
    lesson: WorkbenchLessonHeader | None
    elements: list[dict[str, object]]
    sourceFindings: list[WorkbenchSourceFinding]
    blocks: list[WorkbenchSourceBlock]
    draft: WorkbenchDraftStatus
    errors: list[str]
    state: WorkbenchLessonState
    audio: WorkbenchAudioStatus


class WorkbenchCheckResult(TypedDict):
    """Source check findings and their row-resolved notes."""

    findings: list[WorkbenchSourceFinding]
    errors: list[str]
    notes: list[WorkbenchFinding]


class WorkbenchWriteResult(TypedDict):
    """Fresh lesson projection and checks returned after a source write."""

    lesson: WorkbenchLessonPayload
    check: WorkbenchCheckResult


class WorkbenchSaveResult(TypedDict):
    """Commit identity and source checks returned after saving a lesson draft."""

    commit: str
    check: WorkbenchCheckResult


class WorkbenchCurriculumSaveResult(TypedDict):
    """Commit, new curriculum projection and located plan findings."""

    commit: str
    findings: list[CurriculumFinding]
    curriculum: WorkbenchCurriculumPayload


class WorkbenchErrorResponse(TypedDict, total=False):
    """Optional details returned with an unsuccessful workbench request."""

    error: str
    diff: str
    details: str


class WorkbenchJobQueued(TypedDict):
    """Queued or running job poll response."""

    status: Literal["queued", "running"]
    can_cancel: bool
    steps: NotRequired[list[str]]
    run_id: NotRequired[str]
    turn_id: NotRequired[str]
    partial: NotRequired[str]


class WorkbenchJobDone(TypedDict):
    """Completed job poll response."""

    status: Literal["done"]
    result: object
    can_cancel: Literal[False]
    steps: NotRequired[list[str]]
    run_id: NotRequired[str]


class WorkbenchJobFailed(TypedDict):
    """Failed job poll response."""

    status: Literal["failed"]
    error: str
    error_detail: str
    can_cancel: Literal[False]
    steps: NotRequired[list[str]]
    run_id: NotRequired[str]
    check: NotRequired[dict[str, object]]


class WorkbenchJobCancelled(TypedDict):
    """Cancelled job poll response."""

    status: Literal["cancelled"]
    can_cancel: Literal[False]
    steps: NotRequired[list[str]]
    run_id: NotRequired[str]


WorkbenchJobResponse = WorkbenchJobQueued | WorkbenchJobDone | WorkbenchJobFailed | WorkbenchJobCancelled


class WorkbenchRegenerationResponse(RegenerationResponse):
    """Workflow progress plus the active process job exposed by the run route."""

    job_id: NotRequired[str]


class WorkbenchJobTicket(TypedDict):
    """Identifier returned immediately after scheduling a job."""

    job_id: str


class WorkbenchChatJobTicket(WorkbenchJobTicket):
    """Job and persisted chat turn identifiers returned when chat starts."""

    turn_id: str


class WorkbenchRegenerationTicket(TypedDict):
    """Run and job identifiers returned after starting regeneration."""

    run_id: str
    job_id: str


class WorkbenchBooleanResult(TypedDict):
    """One boolean lifecycle result such as reset or cancellation."""

    cancelled: bool


class WorkbenchResetResult(TypedDict):
    """Successful chat transcript reset response."""

    reset: Literal[True]


class WorkbenchLessonActionResult(TypedDict, total=False):
    """Successful authoring action response; keys retain their route's wire casing."""

    message: str
    lesson: WorkbenchLessonPayload
    commit: str
    dist_files: list[str]
    plan: WorkbenchAudioPlan
    proposal: Proposal
    stages: list[str]
    run_id: str
    job_id: str
    turn_id: str
    cancelled: bool
    reset: bool


class EmptyRequest(TypedDict):
    """Empty JSON request body used by lifecycle routes."""


class BlockSourceWriteRequest(TypedDict):
    """Whole source replacement for one lesson block."""

    source: str


class BlockFieldsWriteRequest(TypedDict):
    """Field replacement for one supported lesson block form."""

    fields: dict[str, str]


WorkbenchBlockWriteRequest = BlockSourceWriteRequest | BlockFieldsWriteRequest


class FileWriteRequest(TypedDict):
    """Whole-file source replacement request."""

    text: str


class CurriculumPreviewRequest(TypedDict):
    """Staged curriculum edit preview request."""

    edits: list[CurriculumEdit]


class CurriculumSaveRequest(TypedDict):
    """Save edits against their exact preview."""

    edits: list[CurriculumEdit]
    review_digest: str | None


class CurriculumChatRequest(TypedDict):
    """Curriculum chat prompt."""

    message: str


class LessonChatRequest(TypedDict):
    """Lesson chat prompt and selected authoring context."""

    message: str
    chips: NotRequired[list[ContextReference]]


class ExplainRequest(TypedDict):
    """Author request to explain one source block in English."""

    block_id: str


class ReviewRequest(TypedDict):
    """Lesson review scope and optional row key."""

    scope: Literal["lesson", "section", "item"]
    key: NotRequired[str]


class FindingResolveRequest(TypedDict, total=False):
    """Finding selection with a required owner reason and optional comment."""

    note: str
    reason: Required[ResolutionReason]
    comment: str
    finding_ids: list[str]


class AudioPlanRequest(TypedDict, total=False):
    """Audio preview selection before synthesis confirmation."""

    scope: Literal["block", "missing_and_stale"]
    selection: Literal["missing", "stale", "missing_and_stale"]
    block_key: str


class AudioSynthesizeRequest(TypedDict):
    """Single-use audio plan token confirmed for synthesis."""

    plan_token: str


class DiscardRequest(TypedDict, total=False):
    """Explicit draft discard confirmation."""

    confirmed: bool


class ScopeRegenerationRequest(TypedDict):
    """Regeneration request for one lesson or section scope."""

    mode: Literal["scope"]
    key: str


class ExercisesRegenerationRequest(TypedDict):
    """Regeneration request for selected exercises."""

    mode: Literal["exercises"]
    exercise_keys: list[str]


WorkbenchRegenerationRequest = ScopeRegenerationRequest | ExercisesRegenerationRequest


class KeepRequest(TypedDict, total=False):
    """Explicit owner reason and optional comment for a Keep as is action."""

    note: str
    reason: Required[KeepReason]
    comment: str


class WorkbenchAudioPlanActionResponse(TypedDict):
    """Audio planning response returned before synthesis starts."""

    plan: WorkbenchAudioPlan


WorkbenchActionResponse = (
    WorkbenchLessonActionResult
    | WorkbenchJobTicket
    | WorkbenchChatJobTicket
    | WorkbenchRegenerationTicket
    | WorkbenchBooleanResult
    | WorkbenchResetResult
    | WorkbenchAudioPlanActionResponse
    | CurriculumEditPreview
)


__all__ = [
    "AudioPlanRequest",
    "AudioSynthesizeRequest",
    "CurriculumChatRequest",
    "CurriculumPreviewRequest",
    "CurriculumSaveRequest",
    "DiscardRequest",
    "EmptyRequest",
    "ExplainRequest",
    "FindingResolveRequest",
    "FileWriteRequest",
    "KeepRequest",
    "LessonChatRequest",
    "ReviewRequest",
    "WorkbenchAudioPlanActionResponse",
    "WorkbenchActionResponse",
    "WorkbenchAudioStatus",
    "WorkbenchBlockWriteRequest",
    "WorkbenchChatJobTicket",
    "WorkbenchCheckResult",
    "WorkbenchCurriculumChecks",
    "WorkbenchCurriculumPayload",
    "WorkbenchCurriculumSaveResult",
    "WorkbenchDraftStatus",
    "WorkbenchErrorResponse",
    "WorkbenchJobResponse",
    "WorkbenchJobTicket",
    "WorkbenchLessonActionResult",
    "WorkbenchLessonPayload",
    "WorkbenchRegenerationRequest",
    "WorkbenchRegenerationResponse",
    "WorkbenchRegenerationTicket",
    "WorkbenchResetResult",
    "WorkbenchSaveResult",
    "WorkbenchSettingsResponse",
    "WorkbenchSourcePayload",
    "WorkbenchWriteResult",
    "WorkbenchAudioPlan",
    "WorkbenchLessonState",
    "Proposal",
    "ContextReference",
    "StoredChatTurn",
    "NewLessonRequest",
    "WorkbenchSettings",
]
