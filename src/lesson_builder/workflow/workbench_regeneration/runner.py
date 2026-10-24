"""Entry points: `start_regeneration`, `execute_regeneration`, `read_regeneration`, `resume_regeneration`, and `recoverable_regenerations` for web dispatch and startup recovery."""

from __future__ import annotations

import logging
import time
from _thread import RLock
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from langgraph.types import StateSnapshot

from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_regeneration import capture_regeneration
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.workflow.workbench_regeneration.graph import build_regeneration_graph
from lesson_builder.workflow.workbench_regeneration.settings import K_WORKBENCH_REGENERATION_RUN_DEADLINE_SECONDS
from lesson_builder.workflow.workbench_regeneration.settings import K_WORKBENCH_REGENERATION_RUN_ID_LENGTH
from lesson_builder.workflow.workbench_regeneration.state import RegenerationResponse
from lesson_builder.workflow.workbench_regeneration.state import RunOutcome
from lesson_builder.workflow.workbench_regeneration.state import RunOutcomeStatus
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_transaction import contained_workbench_path


def start_regeneration(
    root: Path,
    lesson_id: str,
    key: str | None,
    lock: RLock,
    *,
    mode: Literal["scope", "exercises"] = "scope",
    exercise_keys: list[str] | None = None,
) -> str:
    """Persist the captured input before returning a ticket for background execution."""
    with lock:
        captured = capture_regeneration(root, lesson_id, key, exercise_keys, mode=mode)
        identity = uuid4().hex
        path = _run_path(root, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(path)) as saver:
            graph = build_regeneration_graph(root, lock, saver)
            graph.update_state(
                {"configurable": {"thread_id": identity}},
                {
                    **captured,
                    "run_id": identity,
                    "run_outcome": _run_outcome("running"),
                },
                as_node=START,
            )
    return identity


def execute_regeneration(
    root: Path, identity: str, lock: RLock, *, cancellation: CancellationToken | None = None
) -> RegenerationResponse:
    """Resume from the last checkpoint; completed nodes survive process restart."""
    path = _require_run(root, identity)
    current = read_regeneration(root, identity, lock)
    if current["status"] in ("cancelled", "failed", "done", "waiting"):
        return current
    _ensure_run_deadline(root, identity, lock)
    cancellation = cancellation or CancellationToken()
    with SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_regeneration_graph(root, lock, saver, cancellation)
        config: RunnableConfig = {"configurable": {"thread_id": identity}}
        _persist_run_outcome(graph, config, _run_outcome("running"))
        try:
            graph.invoke(None, config)
        except Cancelled as exc:
            status: RunOutcomeStatus = (
                "cancelled"
                if exc.reason == "author"
                else "shutdown_interrupted"
                if exc.reason == "shutdown"
                else "failed"
            )
            _persist_run_outcome(graph, config, _run_outcome(status, str(exc)))
            if exc.reason != "deadline":
                raise
        except Exception as exc:
            _persist_run_outcome(graph, config, _run_outcome("failed", str(exc)))
            raise
    return read_regeneration(root, identity, lock)


def read_regeneration(root: Path, identity: str, lock: RLock) -> RegenerationResponse:
    """Expose node-by-node progress and the proposal interrupt without provider calls."""
    state = _read_regeneration_state(root, identity, lock, _require_run(root, identity))
    if not state.values or not state.values.get("lesson_id"):
        logging.getLogger(__name__).warning("Workbench regeneration run %s has no saved checkpoint", identity)
        return _empty_regeneration_result(identity)
    outcome = _stored_run_outcome(state.values.get("run_outcome"), state)
    errors = [outcome["message"]] if outcome["message"] else []
    error_kinds: list[str] = [str(outcome["status"])] if outcome["message"] else []
    return {
        "run_id": identity,
        "lesson_id": state.values["lesson_id"],
        "key": state.values.get("key"),
        "mode": state.values.get("mode", "exercises" if state.values.get("exercise_keys") else "scope"),
        "status": outcome["status"],
        "steps": state.values.get("steps", []),
        "next": list(state.next),
        "errors": errors,
        "error_kinds": error_kinds,
        "proposal_id": (state.values.get("proposal") or {}).get("proposal_id"),
        "check": (state.values.get("proposal") or {}).get("check"),
        "review": state.values.get("review"),
        "decision": state.values.get("decision"),
        "kept_exercises": state.values.get("kept_exercises", []),
    }


def resume_regeneration(root: Path, proposal: Proposal, lock: RLock) -> None:
    """Complete a waiting graph only once all proposal blocks have an owner decision."""
    if not proposal.run_id or proposal.status == "open":
        return
    identity = proposal.run_id
    current = read_regeneration(root, identity, lock)
    if current["status"] in ("cancelled", "failed", "done", "shutdown_interrupted"):
        return
    path = _require_run(root, identity)
    with lock, SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_regeneration_graph(root, lock, saver)
        config: RunnableConfig = {"configurable": {"thread_id": identity}}
        state = graph.get_state(config)
        if not state.interrupts and "interrupt" in state.next:
            graph.invoke(None, config)
            state = graph.get_state(config)
        if state.interrupts:
            graph.invoke(Command(resume=_owner_decision(proposal)), config)


def recoverable_regenerations(root: Path, lock: RLock) -> tuple[str, ...]:
    """Find running or shutdown-interrupted runs; waiting and terminal outcomes remain parked."""
    directory = contained_workbench_path(root, WorkspacePaths(root).workbench_runs_root)
    recoverable = []
    for path in directory.glob("*.sqlite"):
        identity = path.stem
        if not _is_run_id(identity):
            logging.getLogger(__name__).warning(
                "Skipping workbench regeneration file with invalid run id: %s", path.name
            )
            continue
        try:
            run = read_regeneration(root, identity, lock)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Skipping unreadable workbench regeneration run %s (%s)", path.name, type(exc).__name__
            )
            continue
        if run["status"] in ("running", "shutdown_interrupted"):
            recoverable.append(identity)
    return tuple(recoverable)


def _read_regeneration_state(root: Path, identity: str, lock: RLock, path: Path) -> StateSnapshot:
    """Read a run checkpoint and settle owner decisions already recorded in its proposal."""
    with lock, SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_regeneration_graph(root, lock, saver)
        config: RunnableConfig = {"configurable": {"thread_id": identity}}
        state = graph.get_state(config)
        if state.interrupts:
            proposals = read_workbench_record(root, "proposals", state.values["lesson_id"], ProposalLog)
            settled = next(
                (item for item in proposals.proposals if item.run_id == identity and item.status != "open"), None
            )
            if settled:
                graph.invoke(Command(resume=_owner_decision(settled)), config)
                state = graph.get_state(config)
    return state


def _ensure_run_deadline(root: Path, identity: str, lock: RLock) -> None:
    """Give a pre-deadline checkpoint one persisted run budget before resuming it."""
    path = _require_run(root, identity)
    with lock, SqliteSaver.from_conn_string(str(path)) as saver:
        graph = build_regeneration_graph(root, lock, saver)
        config: RunnableConfig = {"configurable": {"thread_id": identity}}
        state = graph.get_state(config)
        if state.values and not state.values.get("run_deadline_at"):
            graph.update_state(
                config,
                {
                    "run_deadline_at": time.time() + K_WORKBENCH_REGENERATION_RUN_DEADLINE_SECONDS,
                    "run_deadline_seconds": K_WORKBENCH_REGENERATION_RUN_DEADLINE_SECONDS,
                },
            )


def _run_outcome(status: RunOutcomeStatus, message: str | None = None) -> RunOutcome:
    return {"status": status, "message": message}


def _run_outcome_after_graph(state: StateSnapshot) -> RunOutcome:
    if state.interrupts:
        return _run_outcome("waiting")
    errors = [str(task.error) for task in state.tasks if task.error]
    if errors:
        return _run_outcome("failed", errors[0])
    return _run_outcome("running" if state.next else "done")


def _persist_run_outcome(
    graph: CompiledStateGraph[Any, Any, Any, Any], config: RunnableConfig, outcome: RunOutcome
) -> None:
    graph.update_state(config, {"run_outcome": outcome})


def _stored_run_outcome(value: object, state: StateSnapshot) -> RunOutcome:
    if state.interrupts:
        return _run_outcome("waiting")
    if isinstance(value, dict):
        status = value.get("status")
        message = value.get("message")
        if status in ("running", "waiting", "done", "failed", "cancelled", "shutdown_interrupted"):
            if status == "waiting":
                return _run_outcome_after_graph(state)
            return _run_outcome(cast(RunOutcomeStatus, status), message if isinstance(message, str) else None)
    return _run_outcome_after_graph(state)


def _run_path(root: Path, identity: str) -> Path:
    if not _is_run_id(identity):
        raise ValueError("invalid regeneration run id")
    return contained_workbench_path(root, WorkspacePaths(root).workbench_runs_root / f"{identity}.sqlite")


def _is_run_id(identity: str) -> bool:
    """Accept only UUID hex identifiers used for regeneration checkpoints."""
    return len(identity) == K_WORKBENCH_REGENERATION_RUN_ID_LENGTH and all(
        character in "0123456789abcdef" for character in identity
    )


def _empty_regeneration_result(identity: str) -> RegenerationResponse:
    """Expose a checkpoint-free run as a failed terminal outcome."""
    return {
        "run_id": identity,
        "lesson_id": "",
        "key": None,
        "mode": "scope",
        "status": "failed",
        "steps": [],
        "next": [],
        "errors": ["Regeneration run has no saved checkpoint."],
        "error_kinds": ["failed"],
        "proposal_id": None,
        "check": None,
        "review": None,
        "decision": None,
        "kept_exercises": [],
    }


def _owner_decision(proposal: Proposal) -> str:
    if proposal.status == "superseded":
        return "superseded"
    if all(block.status == "accepted" for block in proposal.blocks):
        return "accept"
    if all(block.status == "rejected" for block in proposal.blocks):
        return "reject"
    return "mixed"


def _require_run(root: Path, identity: str) -> Path:
    path = _run_path(root, identity)
    if not path.is_file():
        raise ValueError("regeneration run not found")
    return path
