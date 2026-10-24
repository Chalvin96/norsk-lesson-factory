"""Entry point: `build_regeneration_graph`, which calls `route_after_start`, `route_after_assembly`, `route_after_check`, and `route_after_auto_fix`."""

from __future__ import annotations

import time
from _thread import RLock
from collections.abc import Callable
from pathlib import Path
from queue import Empty
from queue import Queue
from threading import Thread
from typing import Any
from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from langgraph.types import interrupt

from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseStale
from lesson_builder.application.operations.workbench_exercise_alignment import restore_pin
from lesson_builder.application.operations.workbench_proposals import store_proposal
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPin
from lesson_builder.application.operations.workbench_records import ExerciseSectionChange
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposedEdit
from lesson_builder.application.operations.workbench_records import ReviewRun
from lesson_builder.application.operations.workbench_regeneration import assemble_regeneration
from lesson_builder.application.operations.workbench_regeneration import check_regeneration
from lesson_builder.application.operations.workbench_regeneration import generate_regeneration
from lesson_builder.application.operations.workbench_regeneration import generate_regeneration_item
from lesson_builder.application.operations.workbench_regeneration import review_regeneration
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.llm.invocation import provider_call_cancellation
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.workflow.workbench_regeneration.settings import K_WORKBENCH_REGENERATION_PROVIDER_POLL_SECONDS
from lesson_builder.workflow.workbench_regeneration.settings import K_WORKBENCH_REGENERATION_RUN_DEADLINE_SECONDS
from lesson_builder.workflow.workbench_regeneration.state import RegenerationState


class RegenerationRunner:
    """Cohesive injected node runner owns checkpoint transformations and the human gate."""

    def __init__(self, root: Path, lock: RLock, cancellation: CancellationToken | None) -> None:
        self.root = root
        self.lock = lock
        self.cancellation = cancellation or CancellationToken()

    def require_active(self, state: RegenerationState) -> None:
        """Stop when the author cancels or the persisted run deadline expires."""
        self.cancellation.raise_if_cancelled()
        deadline_at = state.get("run_deadline_at")
        if isinstance(deadline_at, (int, float)) and time.time() >= deadline_at:
            seconds = state.get("run_deadline_seconds", K_WORKBENCH_REGENERATION_RUN_DEADLINE_SECONDS)
            self.cancellation.cancel("deadline", f"Regeneration exceeded its {seconds // 60}-minute run deadline.")
            self.cancellation.raise_if_cancelled()

    def generate(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        proposal = self._invoke_provider(state, lambda: generate_regeneration(self.root, dict(state)))
        self.require_active(state)
        proposal.proposal_id = state["run_id"]
        proposal.run_id = state["run_id"]
        return {"proposal": proposal.model_dump(mode="json"), "steps": [*state["steps"], "generate"]}

    def generate_exercise(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        key = state["exercise_key"]
        try:
            edit = self._invoke_provider(
                state,
                lambda: generate_regeneration_item(self.root, dict(state), key, repair=state.get("repair", False)),
            )
        except Cancelled:
            raise
        except Exception:
            if not state.get("repair", False):
                raise
            self.require_active(state)
            return {"exercise_results": {key: state["exercise_results"][key]}}
        self.require_active(state)
        if (
            state.get("repair", False)
            and edit.after_source == state["exercise_context"][key]["current_exercise_source"]
        ):
            kept = dict(state["exercise_results"][key])
            kept["after_source"] = state["exercise_context"][key]["current_exercise_source"]
            return {"exercise_results": {key: kept}}
        return {"exercise_results": {key: edit.model_dump(mode="json")}}

    def assemble_exercises(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        edits = [ProposedEdit.model_validate(state["exercise_results"][key]) for key in state["exercise_keys"]]
        proposal = assemble_regeneration(dict(state), edits)
        kept_exercises = [
            edit.key
            for edit in edits
            if edit.after_source == state["exercise_context"][edit.key]["current_exercise_source"]
        ]
        _record_unchanged_exercise_alignments(
            self.root, state["lesson_id"], self.lock, kept_exercises, state["exercise_context"], state["run_id"]
        )
        steps = [*state["steps"], "generate"] if not state.get("repair", False) else state["steps"]
        if state.get("repair", False):
            if proposal is not None:
                previous = Proposal.model_validate(state["proposal"])
                proposal.proposal_id = previous.proposal_id
                proposal.retries = [*previous.retries, previous.check]
        elif proposal is not None:
            proposal.proposal_id = state["run_id"]
        if proposal is None:
            return {
                "proposal": None,
                "kept_exercises": kept_exercises,
                "decision": "kept unchanged by regeneration",
                "steps": steps,
                "run_outcome": {"status": "done", "message": None},
            }
        proposal.run_id = state["run_id"]
        return {
            "proposal": proposal.model_dump(mode="json"),
            "kept_exercises": kept_exercises,
            "steps": steps,
        }

    def check(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        proposal = Proposal.model_validate(state["proposal"])
        proposal.check = check_regeneration(self.root, dict(state), proposal)
        self.require_active(state)
        for block in proposal.blocks:
            block.check = proposal.check
        return {"proposal": proposal.model_dump(mode="json"), "steps": [*state["steps"], "check"]}

    def auto_fix(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        if _is_exercise_mode(state):
            proposal = Proposal.model_validate(state["proposal"])
            return {
                "repair": True,
                "repair_exercise_keys": [block.key for block in proposal.blocks],
                "steps": [*state["steps"], "fix"],
            }
        previous = Proposal.model_validate(state["proposal"])
        try:
            repaired = self._invoke_provider(state, lambda: generate_regeneration(self.root, dict(state), repair=True))
        except Cancelled:
            raise
        except Exception:
            self.require_active(state)
            return {"proposal": previous.model_dump(mode="json"), "steps": [*state["steps"], "fix"]}
        self.require_active(state)
        repaired.proposal_id = previous.proposal_id
        repaired.run_id = state["run_id"]
        repaired.retries = [previous.check]
        repaired.check = check_regeneration(self.root, dict(state), repaired)
        for block in repaired.blocks:
            block.check = repaired.check
        return {"proposal": repaired.model_dump(mode="json"), "steps": [*state["steps"], "fix"]}

    def review(self, state: RegenerationState) -> dict[str, object]:
        self.require_active(state)
        proposal = Proposal.model_validate(state["proposal"])
        report = self._invoke_provider(
            state,
            lambda: review_regeneration(self.root, dict(state), proposal, cancellation=self.cancellation),
        )
        self.require_active(state)
        if "digest" in report and "reviewer_version" in report:
            proposal.candidate_review = ReviewRun.model_validate(report)
        with self.lock:
            store_proposal(self.root, state["lesson_id"], proposal)
        return {
            "review": report,
            "steps": [*state["steps"], "review", "waiting for you"],
            "run_outcome": {"status": "waiting", "message": None},
        }

    def owner_gate(self, state: RegenerationState) -> dict[str, object]:
        proposal = state.get("proposal")
        if proposal is None:
            raise ValueError("Regeneration cannot wait without a proposal")
        decision = interrupt({"proposal_id": proposal["proposal_id"], "check": proposal["check"]})
        if decision not in ("accept", "reject", "mixed", "superseded"):
            raise ValueError("Regeneration resume requires an owner proposal decision")
        return {"decision": decision}

    def resume(self, state: RegenerationState) -> dict[str, object]:
        return {
            "steps": [*state["steps"], "resume"],
            "decision": state["decision"],
            "run_outcome": {"status": "done", "message": None},
        }

    def _invoke_provider[T](self, state: RegenerationState, operation: Callable[[], T]) -> T:
        """Run a provider operation while observing cancellation and the run deadline."""
        result_queue: Queue[tuple[object | None, Exception | None]] = Queue(maxsize=1)

        def invoke() -> None:
            try:
                with provider_call_cancellation(self.cancellation):
                    result = operation()
                result_queue.put((result, None))
            except Exception as exc:
                result_queue.put((None, exc))

        Thread(target=invoke, name="workbench-regeneration-provider", daemon=True).start()
        while True:
            self.require_active(state)
            deadline_at = state.get("run_deadline_at")
            wait_seconds = K_WORKBENCH_REGENERATION_PROVIDER_POLL_SECONDS
            if isinstance(deadline_at, (int, float)):
                wait_seconds = min(wait_seconds, max(0.0, deadline_at - time.time()))
            try:
                result, error = result_queue.get(timeout=wait_seconds)
            except Empty:
                continue
            if error is not None:
                self.require_active(state)
                raise error
            return cast(T, result)


def build_regeneration_graph(
    root: Path,
    lock: RLock,
    checkpointer: BaseCheckpointSaver[Any],
    cancellation: CancellationToken | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Route the captured scope, attempt at most one repair and park until an owner decision."""
    runner = RegenerationRunner(root, lock, cancellation)
    graph = StateGraph(RegenerationState)
    graph.add_node("generate", runner.generate)
    graph.add_node("generate_exercise", runner.generate_exercise)
    graph.add_node("assemble_exercises", runner.assemble_exercises)
    graph.add_node("check", runner.check)
    graph.add_node("auto_fix", runner.auto_fix)
    graph.add_node("review", runner.review)
    graph.add_node("interrupt", runner.owner_gate)
    graph.add_node("resume", runner.resume)
    graph.add_conditional_edges(START, route_after_start)
    graph.add_edge("generate", "check")
    graph.add_edge("generate_exercise", "assemble_exercises")
    graph.add_conditional_edges("assemble_exercises", route_after_assembly, {"check": "check", "done": END})
    graph.add_conditional_edges("check", route_after_check, {"auto_fix": "auto_fix", "review": "review"})
    graph.add_conditional_edges("auto_fix", route_after_auto_fix)
    graph.add_edge("review", "interrupt")
    graph.add_edge("interrupt", "resume")
    graph.add_edge("resume", END)
    return graph.compile(checkpointer=checkpointer)


def route_after_start(state: RegenerationState) -> str | list[Send]:
    """Send each selected exercise through its own generator or use scoped generation."""
    return _build_exercise_sends(state) if _is_exercise_mode(state) else "generate"


def route_after_assembly(state: RegenerationState) -> str:
    """Skip check and owner review when every generated exercise remains unchanged."""
    return "check" if state.get("proposal") is not None else "done"


def route_after_check(state: RegenerationState) -> str:
    """A failed candidate gets one repair node; a passed one proceeds to review."""
    proposal = state.get("proposal")
    if proposal is None:
        raise ValueError("Regeneration check requires a proposal")
    check = proposal.get("check")
    if not isinstance(check, dict):
        raise TypeError("Regeneration proposal has no check result")
    return "auto_fix" if check.get("status") == "failed" and not state.get("repair") else "review"


def route_after_auto_fix(state: RegenerationState) -> str | list[Send]:
    """Regenerate every selected exercise once against the failed whole-set check; other scopes go to review."""
    return _build_exercise_sends(state) if _is_exercise_mode(state) else "review"


def _record_unchanged_exercise_alignments(
    root: Path,
    lesson_id: str,
    lock: RLock,
    kept_exercises: list[str],
    exercise_context: dict[str, ExerciseAlignmentPin],
    run_id: str,
) -> None:
    """Pin only exercises whose captured inputs remain the latest source decision."""
    with lock:
        current_source = read_workbench_source(root, lesson_id)
        current_exercises = {
            block.key: block for block in split_exercise_source(current_source.files["exercises.yaml"])
        }
        alignment_service = ExerciseAlignment(root, lesson_id)
        current_alignment = {
            state.exercise_key: state
            for state in alignment_service.states(current_source)
            if state.exercise_key is not None
        }
        for key in kept_exercises:
            context = exercise_context[key]
            if not _is_captured_exercise_current(key, context, current_exercises, current_alignment):
                continue
            alignment_service.pin(
                restore_pin(key, context),
                "kept",
                note="Kept unchanged by regeneration.",
                decision_id=f"{run_id}:{key}",
            )


def _is_captured_exercise_current(
    key: str,
    context: ExerciseAlignmentPin,
    current_exercises: dict[str, Any],
    current_alignment: dict[str, Any],
) -> bool:
    """Match the captured exercise and changed section diff against the live draft."""
    current = current_exercises.get(key)
    stale = current_alignment.get(key)
    if not isinstance(stale, ExerciseStale) or current is None:
        return False
    captured_changes = [ExerciseSectionChange.model_validate(item) for item in context["changed_sections"]]
    return current.source == context["current_exercise_source"] and list(stale.changed_sections) == captured_changes


def _build_exercise_sends(state: RegenerationState) -> list[Send]:
    keys = state.get("repair_exercise_keys", []) if state.get("repair") else state["exercise_keys"]
    return [Send("generate_exercise", {**state, "exercise_key": key}) for key in keys]


def _is_exercise_mode(state: RegenerationState) -> bool:
    return state.get("mode", "exercises" if state.get("exercise_keys") else "scope") == "exercises"
