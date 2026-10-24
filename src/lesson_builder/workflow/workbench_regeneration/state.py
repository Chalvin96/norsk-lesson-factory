"""Not a check itself — state and terminal error types for regeneration runs."""

from __future__ import annotations

import operator
from typing import Annotated
from typing import Literal
from typing import TypedDict

from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPin
from lesson_builder.application.operations.workbench_records import ProposalCheck

RunOutcomeStatus = Literal["running", "waiting", "done", "failed", "cancelled", "shutdown_interrupted"]


class RunOutcome(TypedDict):
    """One persisted status and its optional terminal explanation."""

    status: RunOutcomeStatus
    message: str | None


class RegenerationState(TypedDict, total=False):
    """Captured scope, node results and the final owner decision."""

    run_id: str
    run_deadline_at: float
    run_deadline_seconds: int
    lesson_id: str
    key: str | None
    mode: Literal["scope", "exercises"]
    scope: str
    files: dict[str, str]
    selected_keys: list[str]
    prompt: str
    exercise_keys: list[str]
    exercise_context: dict[str, ExerciseAlignmentPin]
    repair_exercise_keys: list[str]
    exercise_key: str
    exercise_results: Annotated[dict[str, dict[str, object]], operator.or_]
    kept_exercises: list[str]
    repair: bool
    proposal: dict[str, object] | None
    review: dict[str, object]
    steps: list[str]
    decision: str | None
    run_outcome: RunOutcome


class RegenerationResponse(TypedDict):
    """Stable workflow progress snapshot returned to the workbench HTTP adapter."""

    run_id: str
    lesson_id: str
    key: str | None
    mode: Literal["scope", "exercises"]
    status: RunOutcomeStatus
    steps: list[str]
    next: list[str]
    errors: list[str]
    error_kinds: list[str]
    proposal_id: str | None
    check: ProposalCheck | None
    review: object | None
    decision: str | None
    kept_exercises: list[str]
