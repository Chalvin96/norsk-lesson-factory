"""Entry point: ``run_lesson_package`` / ``resume_lesson_package`` / ``show_lesson_package`` / ``list_lesson_packages``.

The execution core for the lesson-package graph, kept separate from the
argparse shell (``cli.py``) so the CLI and any programmatic caller depend on
this driver, not on the CLI module.

Output layout: all writes go under ``store/scratch/catalog_generation/<run_id>/``
(gitignored). The tracked ``data/``, ``dist/``, and ``knowledge/``,
and planning-history trees are never touched. The checkpoint DB lives under
``store/catalog_generation_checkpoints.db`` and is shared across runs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from lesson_builder.workflow.lesson_generation.artifacts import build_checkpoints_path
from lesson_builder.workflow.lesson_generation.artifacts import build_run_output_root
from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.graph import build_lesson_generation_graph
from lesson_builder.workflow.lesson_generation.review_edit import review_existing_package
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISIONS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DEFAULT_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_HASH_ALGO
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_HASH_PREFIX
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_HUMAN_GATE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_PREPARE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_RUN_ID_MAX_LENGTH
from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations
from lesson_builder.workflow.lesson_generation.state import K_LESSON_GENERATION_THREAD_PREFIX
from lesson_builder.workflow.lesson_generation.state import thread_id_for
from lesson_builder.workspace.paths import K_WORKSPACE_ROOT

K_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

K_DEFAULT_FIXTURE = K_WORKSPACE_ROOT / "tests" / "fixtures" / "catalog_package" / "fixture"


class LessonPackageThreadNotFoundError(Exception):
    """No checkpointed state exists for the given run_id."""


class LessonPackageThreadNotParkedError(Exception):
    """The thread exists but is not currently parked at the human gate."""


def run_lesson_package(
    *,
    repo_root: Path | None = None,
    run_id: str | None = None,
    fixture_source: Path | None = None,
    deps: LessonGenerationDeps | None = None,
    generate: bool = False,
    job: str = K_LESSON_GENERATION_DEFAULT_JOB,
    stop_after_generation: bool = False,
    curriculum_slot_sha256: str | None = None,
    catalog_id: str | None = None,
) -> dict[str, Any]:
    """Start a lesson-package run. Parks at the human gate (interrupt).

    ``repo_root`` defaults to the real repo root. ``fixture_source`` defaults
    to the checked-in lesson-package fixture under
    ``tests/fixtures/catalog_package/fixture``.
    ``run_id`` defaults to a generated uuid hex prefix.

    When ``generate`` is set, the package is authored by the checkpointed rich
    stages configured on ``LessonGenerationDeps``.
    ``stop_after_generation`` is used by the batch driver to leave the graph
    checkpointed immediately before source preparation; the same run is then
    resumed for compilation and the human gate.
    """
    root = Path(repo_root) if repo_root else K_WORKSPACE_ROOT
    chosen_run_id = _validated_run_id(run_id)
    requested_fixture = Path(fixture_source) if fixture_source else None
    default_fixture = requested_fixture or K_DEFAULT_FIXTURE
    output_root = build_run_output_root(root, chosen_run_id)
    graph_deps = deps or LessonGenerationDeps()
    thread_id = thread_id_for(chosen_run_id)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    with _open_graph(root, graph_deps) as graph:
        checkpoint = graph.get_state(config)
        if not checkpoint.values:
            fixture = default_fixture
            _require_fixture_source(fixture)
            initial_state: dict[str, Any] = {
                "run_id": chosen_run_id,
                "repo_root": str(root),
                "output_root": str(output_root),
                "fixture_source": str(fixture),
                "input_source_hash": _hash_fixture_source(fixture),
                "generation_job": job if generate else None,
                "curriculum_slot_sha256": curriculum_slot_sha256,
                "catalog_id": catalog_id,
                "generated_source_dir": None,
                "llm_receipt_path": None,
            }
            if stop_after_generation:
                if not generate:
                    raise ValueError("stop_after_generation requires generate=True")
                graph.invoke(initial_state, config=config, interrupt_before=[K_LESSON_GENERATION_NODE_PREPARE])
            else:
                graph.invoke(initial_state, config=config)
        elif checkpoint.next and K_LESSON_GENERATION_NODE_HUMAN_GATE not in checkpoint.next:
            values = checkpoint.values
            fixture = requested_fixture or _checkpoint_fixture_source(values)
            _require_fixture_source(fixture)
            _validate_resume_inputs(
                values,
                fixture_source=fixture,
                generate=generate,
                job=job,
                curriculum_slot_sha256=curriculum_slot_sha256,
                catalog_id=catalog_id,
            )
            # A failed node has a durable ``next`` boundary. Re-invoking with
            # no input resumes that node; passing the initial state would
            # incorrectly replay every rich stage before it.
            if stop_after_generation:
                graph.invoke(None, config=config, interrupt_before=[K_LESSON_GENERATION_NODE_PREPARE])
            else:
                graph.invoke(None, config=config)
        elif checkpoint.values:
            values = checkpoint.values
            fixture = requested_fixture or _checkpoint_fixture_source(values)
            _require_fixture_source(fixture)
            _validate_resume_inputs(
                values,
                fixture_source=fixture,
                generate=generate,
                job=job,
                curriculum_slot_sha256=curriculum_slot_sha256,
                catalog_id=catalog_id,
            )
        return _summarize_state(graph, config, chosen_run_id)


def review_lesson_package_edit(
    source_run_id: str,
    *,
    repo_root: Path | None = None,
    run_id: str | None = None,
    reviewer_job: str = K_LESSON_GENERATION_REVIEW_EDIT_JOB,
) -> dict[str, Any]:
    """Review one parked package and park a copied, locally edited result.

    Only the reviewer call and deterministic source edits run here. The draft,
    normalizer, and exercise-author stages are never invoked. The original
    checkpoint and source package remain unchanged. Because an edit invalidates
    the downstream content-addressed attestations, the edited copy is returned
    for a fresh production-chain run instead of being parked as if it had been
    re-reviewed.
    """
    root = Path(repo_root) if repo_root else K_WORKSPACE_ROOT
    original_id = _validated_run_id(source_run_id)
    original_config = {"configurable": {"thread_id": thread_id_for(original_id)}}
    with _open_graph(root) as graph:
        original_state = graph.get_state(original_config)
        if not original_state.values:
            raise LessonPackageThreadNotFoundError(f"no such lesson-package thread {original_id!r}")
        if K_LESSON_GENERATION_NODE_HUMAN_GATE not in (original_state.next or ()):
            raise LessonPackageThreadNotParkedError(
                f"lesson-package thread {original_id!r} is not parked at the human gate"
            )
        values = original_state.values
        candidate = values.get("generated_source_dir") or values.get("fixture_source")
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"lesson-package thread {original_id!r} has no authored source path")
    source_dir = Path(candidate)
    chosen_run_id = _review_run_id(original_id, run_id)
    output_root = build_run_output_root(root, chosen_run_id)
    if output_root.exists():
        raise ValueError(f"review/edit output already exists: {output_root}")
    review_result = review_existing_package(
        source_dir=source_dir,
        output_root=output_root,
        repo_root=root,
        run_id=chosen_run_id,
        reviewer_job=reviewer_job,
    )
    review_summary: dict[str, Any] = {
        "review_edit_status": review_result.status,
        "review_edit_path": str(review_result.review_path),
        "review_edit_receipt_path": str(review_result.receipt_path),
        "review_edit_source_dir": str(review_result.source_dir),
        "review_edit_applied_edits": list(review_result.applied_edits),
        "review_edit_verdict": review_result.review.verdict if review_result.review else None,
        "review_edit_summary": review_result.review.summary if review_result.review else None,
        "original_run_id": original_id,
        "original_source_dir": str(source_dir),
    }
    if review_result.status != "edited":
        return {"run_id": chosen_run_id, **review_summary}
    attestation_issues = verify_source_attestations(review_result.source_dir)
    review_summary["review_edit_status"] = "needs_revalidation"
    review_summary["review_edit_revalidation_issues"] = attestation_issues or [
        "edited source requires a fresh production-chain attestation set"
    ]
    review_summary["stage"] = "not_reached"
    review_summary["next"] = []
    return {"run_id": chosen_run_id, **review_summary}


def resume_lesson_package(
    run_id: str,
    decision: str,
    *,
    repo_root: Path | None = None,
    deps: LessonGenerationDeps | None = None,
) -> dict[str, Any]:
    """Resume a parked lesson-package thread with a human decision.

    ``decision`` is one of ``accept``, ``defer``, or ``reject``. Accept routes
    to finalize (writes export + ledger). Defer and reject end the thread.
    """
    root = Path(repo_root) if repo_root else K_WORKSPACE_ROOT
    chosen_run_id = _validated_run_id(run_id)
    if decision not in K_LESSON_GENERATION_DECISIONS:
        raise ValueError(f"decision must be one of {list(K_LESSON_GENERATION_DECISIONS)!r}")
    thread_id = thread_id_for(chosen_run_id)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    with _open_graph(root, deps) as graph:
        state = graph.get_state(config)
        if not state.values:
            raise LessonPackageThreadNotFoundError(f"no such lesson-package thread {thread_id!r}")
        retrying_finalize = decision == "accept" and tuple(state.next or ()) == ("finalize",)
        if K_LESSON_GENERATION_NODE_HUMAN_GATE not in (state.next or ()) and not retrying_finalize:
            raise LessonPackageThreadNotParkedError(
                f"lesson-package thread {thread_id!r} is not parked at the human gate "
                f"(next={list(state.next) if state.next else []!r}); nothing to resume"
            )
        if retrying_finalize:
            graph.invoke(None, config=config)
        else:
            graph.invoke(Command(resume={"status": decision}), config=config)
        return _summarize_state(graph, config, chosen_run_id)


def show_lesson_package(
    run_id: str,
    *,
    repo_root: Path | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Show the parked (or terminal) state of an existing lesson-package thread."""
    root = Path(repo_root) if repo_root else K_WORKSPACE_ROOT
    chosen_run_id = _validated_run_id(run_id)
    thread_id = thread_id_for(chosen_run_id)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    with _open_graph(root) as graph:
        state = graph.get_state(config)
        if not state.values:
            raise LessonPackageThreadNotFoundError(f"no such lesson-package thread {thread_id!r}")
        summary = _summarize_state(graph, config, chosen_run_id)
        if full:
            summary["export_doc"] = state.values.get("export_doc")
        return summary


def list_lesson_packages(*, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Enumerate lesson-package threads parked at the human gate."""
    root = Path(repo_root) if repo_root else K_WORKSPACE_ROOT
    with _open_graph(root) as graph:
        checkpointer = graph.checkpointer
        checkpoint_configs = [ct.config for ct in checkpointer.list(None)]
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for config in checkpoint_configs:
            thread_id = config["configurable"]["thread_id"]
            if thread_id in seen:
                continue
            seen.add(thread_id)
            state = graph.get_state(config)
            if K_LESSON_GENERATION_NODE_HUMAN_GATE not in (state.next or ()):
                continue
            prefix = K_LESSON_GENERATION_THREAD_PREFIX
            run_id = thread_id[len(prefix) :] if thread_id.startswith(prefix) else thread_id
            values = state.values
            rows.append(
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "stage": values.get("stage"),
                    "source_hash": values.get("source_hash"),
                    "export_hash": (values.get("export_doc") or {}).get("export_hash"),
                }
            )
        return rows


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validated_run_id(run_id: str | None) -> str:
    value = run_id or uuid.uuid4().hex[:12]
    if not K_RUN_ID_RE.fullmatch(value):
        raise ValueError("run_id must be a single safe identifier of at most 64 characters")
    return value


def _require_fixture_source(fixture_source: Path) -> None:
    """Require a checkpoint input directory before hashing or invoking a graph."""
    if not fixture_source.is_dir():
        raise FileNotFoundError(f"lesson-package fixture not found at {fixture_source}")


def _checkpoint_fixture_source(values: Mapping[str, Any]) -> Path:
    """Recover the source path recorded by a checkpoint when callers omit it."""
    fixture_source = values.get("fixture_source")
    if not isinstance(fixture_source, str) or not fixture_source:
        raise ValueError("lesson-package checkpoint has no fixture source")
    return Path(fixture_source)


def _hash_fixture_source(fixture_source: Path) -> str:
    """Hash every input byte and relative name in a fixture directory."""
    root = Path(fixture_source)
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return K_LESSON_GENERATION_HASH_PREFIX + hasher.hexdigest()


def _is_same_path(left: Path, right: Path) -> bool:
    """Compare two scratch paths after resolving relative spelling."""
    return left.resolve(strict=False) == right.resolve(strict=False)


def _validate_resume_inputs(
    values: Mapping[str, Any],
    *,
    fixture_source: Path,
    generate: bool,
    job: str,
    curriculum_slot_sha256: str | None,
    catalog_id: str | None,
) -> None:
    """Fail closed when a retry changes the checkpoint's immutable inputs."""
    mismatches: list[str] = []
    expected_source_hash = values.get("input_source_hash")
    generated_source = values.get("generated_source_dir")
    is_generated_source = isinstance(generated_source, str) and _is_same_path(fixture_source, Path(generated_source))
    if not is_generated_source:
        actual_source_hash = _hash_fixture_source(fixture_source)
        if actual_source_hash != expected_source_hash:
            mismatches.append(
                f"fixture_source hash (checkpoint={expected_source_hash!r}, requested={actual_source_hash!r})"
            )

    expected_job = values.get("generation_job")
    requested_job = job if generate else expected_job
    if requested_job != expected_job:
        mismatches.append(f"generation_job (checkpoint={expected_job!r}, requested={requested_job!r})")

    for name, requested in (
        ("curriculum_slot_sha256", curriculum_slot_sha256),
        ("catalog_id", catalog_id),
    ):
        if requested is not None and requested != values.get(name):
            mismatches.append(f"{name} (checkpoint={values.get(name)!r}, requested={requested!r})")
    if mismatches:
        raise ValueError("cannot resume lesson-package run with changed immutable input(s): " + "; ".join(mismatches))


def _review_run_id(source_run_id: str, requested: str | None) -> str:
    """Choose a collision-safe bounded identifier for a review/edit result."""
    if requested:
        return _validated_run_id(requested)
    suffix = uuid.uuid4().hex[:8]
    prefix = f"{source_run_id}-review-edit-"
    value = f"{prefix}{suffix}"
    if len(value) <= K_LESSON_GENERATION_RUN_ID_MAX_LENGTH:
        return _validated_run_id(value)
    digest = uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:8]
    return _validated_run_id(f"review-edit-{digest}")


@contextlib.contextmanager
def _open_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(connection)
    finally:
        connection.close()


@contextlib.contextmanager
def _open_graph(
    repo_root: Path,
    deps: LessonGenerationDeps | None = None,
) -> Iterator[Any]:
    with _open_checkpointer(build_checkpoints_path(repo_root)) as checkpointer:
        yield build_lesson_generation_graph(deps, checkpointer=checkpointer)


def _summarize_state(
    graph: CompiledStateGraph[Any, Any, Any, Any], config: RunnableConfig, run_id: str
) -> dict[str, Any]:
    state = graph.get_state(config)
    values = state.values
    export_doc = values.get("export_doc") or {}
    coverage = _coverage_receipt(values.get("llm_receipt_path"))
    return {
        "run_id": run_id,
        "thread_id": thread_id_for(run_id),
        "next": list(state.next) if state.next else [],
        "stage": values.get("stage"),
        "source_hash": values.get("source_hash"),
        "scheduled_slot": values.get("scheduled_slot"),
        "export_hash": export_doc.get("export_hash"),
        "semantic_hash": export_doc.get("semantic_hash"),
        "dependency_hash": export_doc.get("dependency_hash"),
        "exercise_diagnostics": values.get("exercise_diagnostics"),
        "source_files": values.get("source_files", []),
        "export_path": values.get("export_path"),
        "ledger_path": values.get("ledger_path"),
        "approval_path": values.get("approval_path"),
        "promotion_source_hash": values.get("promotion_source_hash"),
        "human_decision": values.get("human_decision"),
        "generation_job": values.get("generation_job"),
        "curriculum_slot_sha256": values.get("curriculum_slot_sha256"),
        "catalog_id": values.get("catalog_id"),
        "generated_source_dir": values.get("generated_source_dir"),
        "llm_receipt_path": values.get("llm_receipt_path"),
        "coverage": coverage,
    }


def _coverage_receipt(receipt_path: object) -> dict[str, Any] | None:
    """Read only the compact coverage section for operator summaries."""
    if not isinstance(receipt_path, str) or not receipt_path:
        return None
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"status": "receipt_unavailable"}
    coverage = receipt.get("coverage")
    return coverage if isinstance(coverage, dict) else {"status": "unconfigured"}


__all__ = [
    "run_lesson_package",
    "review_lesson_package_edit",
    "resume_lesson_package",
    "show_lesson_package",
    "list_lesson_packages",
    "build_checkpoints_path",
    "LessonPackageThreadNotFoundError",
    "LessonPackageThreadNotParkedError",
    "K_DEFAULT_FIXTURE",
]
