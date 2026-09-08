"""Entry point: ``LessonGenerationState`` and ``route_after_human``.

Not a check itself -- defines the per-run state schema and the single
human-gate router for the lesson-package graph. The state is
blob-in-state (source files, compiled export with derived transcript and
audio declarations) so a parked run resumes from a checkpoint without
re-reading the fixture.
"""

from __future__ import annotations

from typing import Any
from typing import Literal
from typing import TypedDict

from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_ACCEPT
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_DEFER
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_REJECT
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_FINALIZE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_HUMAN_GATE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_ACCEPTED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_DEFERRED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_PARKED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_PREPARING
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_REJECTED

LessonPackageStage = Literal["preparing", "parked", "accepted", "deferred", "rejected"]

# Router return literals. Kept as module constants so the graph wiring and the
# router agree on the same strings without a circular import.
K_ROUTE_FINALIZE = "finalize"
K_ROUTE_END = "END"
K_LESSON_GENERATION_THREAD_PREFIX = "catalog_generation:"


class LessonGenerationState(TypedDict, total=False):
    """Checkpointable state for one lesson-package run.

    The fixture source path, output root, run identity, and stage are
    runner-owned execution boundaries. The prepare node resolves them into
    the source-copy directory and compiled export document (including derived
    transcript blocks and audio declarations) that downstream nodes and the
    human gate read from state.
    """

    # ── Runner-owned inputs ──────────────────────────────────────────────
    run_id: str
    repo_root: str
    output_root: str
    fixture_source: str
    input_source_hash: str
    generation_job: str | None
    curriculum_slot_sha256: str | None
    catalog_id: str | None
    generated_source_dir: str | None
    llm_receipt_path: str | None

    # ── Checkpointed rich authoring outputs ─────────────────────────────
    generation_stage: str
    rich_draft_text: str
    rich_reviewed_draft_hash: str
    rich_checkpoint_intents_yaml: str
    rich_normalization_prompt: str
    rich_normalized_package: dict[str, Any]
    rich_lesson_review_attestation: dict[str, Any]
    rich_normalization_attestation: dict[str, Any]
    rich_normalization_repaired: bool
    rich_normalization_response_reused: bool
    rich_requests_hash: str
    rich_lesson_hash: str
    rich_exercise_package: dict[str, Any]
    rich_exercises_yaml: str
    rich_exercise_prompt: str
    rich_exercise_response_path: str
    rich_exercise_input_path: str
    rich_exercise_response_reused: bool
    rich_internal_lesson: dict[str, Any]
    rich_verification: dict[str, Any]
    rich_standalone_report: dict[str, Any] | None
    rich_semantic_repairs: int
    rich_coverage: dict[str, Any]

    # ── Prepare outputs ──────────────────────────────────────────────────
    source_dir: str
    source_files: list[str]
    source_hash: str
    scheduled_slot: dict[str, Any]
    export_doc: dict[str, Any]
    exercise_diagnostics: dict[str, Any] | None
    export_path: str

    # ── Lifecycle ────────────────────────────────────────────────────────
    stage: LessonPackageStage
    human_decision: dict[str, Any] | None

    # ── Finalize outputs ─────────────────────────────────────────────────
    ledger_path: str
    ledger_entry: dict[str, Any]
    approval_path: str
    promotion_source_hash: str


def route_after_human(state: LessonGenerationState) -> str:
    """Router after the human gate resumes.

    ``accept`` -> ``finalize`` (write export + ledger + terminal stage).
    ``defer``  -> ``END`` (thread stays parked; stage is ``deferred``).
    ``reject`` -> ``END`` (terminal; stage is ``rejected``).

    An unknown or missing decision keeps the thread parked (re-enters
    ``human_gate``), matching the lesson-QA graph's refusal-to-loop behavior.
    """
    decision = state.get("human_decision") or {}
    status = decision.get("status")
    if status == K_LESSON_GENERATION_DECISION_ACCEPT:
        return K_ROUTE_FINALIZE
    if status in (K_LESSON_GENERATION_DECISION_DEFER, K_LESSON_GENERATION_DECISION_REJECT):
        return K_ROUTE_END
    return K_LESSON_GENERATION_NODE_HUMAN_GATE


def thread_id_for(run_id: str) -> str:
    """Stable thread id: ``f"catalog_generation:{run_id}"``.

    All lesson-package threads share a single course id namespace, so the
    thread id is just the run id under the ``catalog_generation:`` prefix.
    Resilient to a run_id that already carries the prefix.
    """
    prefix = K_LESSON_GENERATION_THREAD_PREFIX
    if run_id.startswith(prefix):
        run_id = run_id[len(prefix) :]
    return f"{prefix}{run_id}"


__all__ = [
    "LessonPackageStage",
    "LessonGenerationState",
    "route_after_human",
    "thread_id_for",
    "K_ROUTE_FINALIZE",
    "K_ROUTE_END",
    "K_LESSON_GENERATION_THREAD_PREFIX",
    "K_LESSON_GENERATION_NODE_HUMAN_GATE",
    "K_LESSON_GENERATION_NODE_FINALIZE",
    "K_LESSON_GENERATION_STAGE_PREPARING",
    "K_LESSON_GENERATION_STAGE_PARKED",
    "K_LESSON_GENERATION_STAGE_ACCEPTED",
    "K_LESSON_GENERATION_STAGE_DEFERRED",
    "K_LESSON_GENERATION_STAGE_REJECTED",
]
