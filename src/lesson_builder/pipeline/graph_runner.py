"""Lesson-QA graph driver/service: ``run_graph`` / ``show_thread`` /
``resume_thread`` / ``list_threads`` plus the scratch-vs-commit IO routing.

This is the execution core for the compiled graph, kept separate from the
argparse shell (``cli.py``) so that flows (``improvement_flow``) and the CLI both
depend on this driver -- not on the CLI module.

Shared flow helpers (``run_back_half`` / ``write_draft``) live here too: both
J1 (cold-author) and J3 (improve) build a draft then feed it to the same
Lesson-QA back-half, so the helper that wires the injected loader + fixer/judge
into ``run_graph`` is shared. Each flow passes its own loader closure so the
mode-specific ``LoadedLesson`` (baseline_export / recorded_requirements_hash)
is built correctly per journey.

Scratch-vs-commit: a bare ``run`` never writes the tracked ``data/lessons/``,
``dist/lessons/``, or ``data/lesson_acceptance_log.jsonl``. It writes lesson/dist
artifacts and the acceptance ledger under ``store/scratch/`` (gitignored)
instead. Pass ``commit=True`` to write the real tracked paths.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from lesson_builder.pipeline.fixers import author_fixer
from lesson_builder.pipeline.judges import default_judge, noop_judge, pedagogy_review
from lesson_builder.pipeline.lesson_qa_graph import (
    Fixer,
    GraphDeps,
    Judge,
    LessonLoader,
    build_lesson_qa_graph,
    default_exporter,
    default_loader,
    noop_fixer,
)
from lesson_builder.pipeline.state import thread_id_for

K_REPO_ROOT = Path(__file__).resolve().parents[3]
K_CHECKPOINTS_PATH = K_REPO_ROOT / "store" / "checkpoints.db"
K_SCRATCH_DIRNAME = "scratch"

# The human gate node name in the compiled graph -- a thread is "parked" (can
# be resumed with a human decision) when this name appears in ``state.next``.
K_HUMAN_GATE_NODE = "human_gate"

# Named fixer choices EXPOSED TO THE CLI/FLOWS. ``codex`` authors real repairs
# via the author agent profile. ``noop`` is intentionally absent (Task F): it
# stays importable from ``lesson_qa_graph`` for tests to inject directly via
# ``GraphDeps(fixer=noop_fixer, ...)`` but is not a selectable runtime option.
# The library default (``run_graph`` with no ``fixer_name``/``deps``) still
# builds an offline-safe noop GraphDeps so ad-hoc direct calls stay hermetic.
K_FIXERS = {"codex": author_fixer}
K_DEFAULT_FIXER = "codex"

# Named judge choices EXPOSED TO THE CLI/FLOWS. ``real`` runs the reviewer-backed
# advisory panel (pedagogy / objective_alignment / answer). ``noop`` is
# intentionally absent (Task F): importable for test injection, not selectable
# at the CLI. The library default (``run_graph`` with no ``judge_name``/``deps``)
# stays offline-safe.
K_JUDGES = {"real": default_judge}
K_DEFAULT_JUDGE = "real"


class ThreadNotFoundError(Exception):
    """No checkpointed state exists for the given slug/run_id."""


class ThreadNotParkedError(Exception):
    """The thread exists but is not currently parked at the human gate."""


def _acceptance_log_path(root: Path) -> Path:
    return root / "data" / "lesson_acceptance_log.jsonl"


def _scratch_root(repo_root: Path) -> Path:
    return repo_root / "store" / K_SCRATCH_DIRNAME


def _resolve(table: dict[str, Any], name: str, label: str) -> Any:
    try:
        return table[name]
    except KeyError as exc:
        raise ValueError(f"unknown {label} {name!r}; choose one of {sorted(table)}") from exc


def _resolve_fixer(fixer_name: str | None) -> Fixer:
    if fixer_name is None:
        return noop_fixer
    return cast("Fixer", _resolve(K_FIXERS, fixer_name, "fixer"))


def _resolve_judge(judge_name: str | None) -> Judge:
    if judge_name is None:
        return noop_judge
    return cast("Judge", _resolve(K_JUDGES, judge_name, "judge"))


def _build_deps(fixer_name: str | None = None, judge_name: str | None = None) -> GraphDeps:
    # Task A: wire the focused pedagogy re-derivation seam only for the live
    # judge panel (the one that produces a real pedagogy review worth re-scoring
    # post-fix). The offline noop judge produces no review, so rejudge stays
    # disabled (None) for the library default.
    rejudge_pedagogy = pedagogy_review if judge_name is not None else None
    return GraphDeps(
        loader=default_loader,
        exporter=default_exporter,
        fixer=_resolve_fixer(fixer_name),
        judge=_resolve_judge(judge_name),
        rejudge_pedagogy=rejudge_pedagogy,
    )


@contextlib.contextmanager
def _open_checkpointer(checkpoints_path: Path) -> Iterator[SqliteSaver]:
    checkpoints_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(checkpoints_path), check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


@contextlib.contextmanager
def _open_graph(
    *,
    deps: GraphDeps | None = None,
    fixer_name: str | None = None,
    judge_name: str | None = None,
) -> Iterator[Any]:
    with _open_checkpointer(K_CHECKPOINTS_PATH) as checkpointer:
        graph_deps = deps or _build_deps(fixer_name, judge_name)
        yield build_lesson_qa_graph(graph_deps, checkpointer=checkpointer)


def run_graph(
    slug: str,
    *,
    repo_root: Path | None = None,
    run_id: str | None = None,
    commit: bool = False,
    fixer_name: str | None = None,
    judge_name: str | None = None,
    deps: GraphDeps | None = None,
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a lesson-QA run for ``slug``. Parks at the human gate (interrupt).

    ``repo_root`` defaults to the real repo root (the loader always reads the
    real lesson/requirements from there). Without ``commit=True``, the
    EXPORT/ledger write target is redirected to a scratch dir under
    ``store/scratch/`` so an ad-hoc run cannot dirty the tracked
    ``data/lessons/``, ``dist/lessons/``, or ``data/lesson_acceptance_log.jsonl``.
    Pass ``commit=True`` to write the real tracked paths under ``repo_root``.

    Task F: ``noop`` is no longer a selectable CLI/registry option. The library
    default (``fixer_name``/``judge_name`` both ``None``) still builds an
    offline-safe noop ``GraphDeps`` so direct programmatic callers stay hermetic;
    pass ``deps=GraphDeps(...)`` to inject test collaborators directly.
    """
    repo_root = Path(repo_root) if repo_root else K_REPO_ROOT
    run_id = run_id or uuid.uuid4().hex[:12]
    if commit:
        output_root = repo_root
        acceptance_log_path = _acceptance_log_path(repo_root)
    else:
        output_root = _scratch_root(repo_root)
        acceptance_log_path = output_root / "lesson_acceptance_log.jsonl"
    config = {"configurable": {"thread_id": thread_id_for(slug, run_id)}}
    payload = {
        "slug": slug,
        "run_id": run_id,
        "repo_root": str(repo_root),
        "acceptance_log_path": str(acceptance_log_path),
        "output_root": str(output_root),
    }
    if initial_state:
        payload.update(initial_state)
    with _open_graph(deps=deps, fixer_name=fixer_name, judge_name=judge_name) as graph:
        graph.invoke(payload, config=config)
        return _summarize_state(graph, config, slug, run_id)


def run_back_half(
    slug: str,
    *,
    loader: LessonLoader,
    repo_root: Path,
    run_id: str,
    commit: bool = False,
    fixer_name: str = K_DEFAULT_FIXER,
    judge_name: str = K_DEFAULT_JUDGE,
    fixer: Fixer | None = None,
    judge: Judge | None = None,
) -> dict[str, Any]:
    """Wire an injected loader + fixer/judge into ``run_graph`` and park at the gate.

    Shared by the cold-author (J1) and improve (J3) flows. Each flow passes its
    OWN loader closure so the mode-specific ``LoadedLesson`` is built correctly:

    - cold-author: returns ``baseline_export=None`` (no trusted baseline for a
      brand-new slug; regression/stale checks no-op).
    - improve: calls ``default_loader`` and PRESERVES ``loaded.baseline_export``
      + ``loaded.recorded_requirements_hash`` (replacing only ``loaded.lesson``
      with the draft). Mis-defaulting these to None silently disables the
      regression check on the improve path.

    ``fixer``/``judge``: prefer directly-injected callables (tests inject
    ``noop_fixer``/``noop_judge`` to stay offline); when ``None``, resolve via
    the named registry entries (``codex``/``real``) for CLI/production use.
    Raises ``ValueError`` on an unknown fixer/judge name.
    """
    resolved_fixer = fixer if fixer is not None else _resolve(K_FIXERS, fixer_name, "fixer")
    resolved_judge = judge if judge is not None else _resolve(K_JUDGES, judge_name, "judge")
    deps = GraphDeps(loader=loader, fixer=resolved_fixer, judge=resolved_judge)
    return run_graph(
        slug,
        repo_root=repo_root,
        run_id=run_id,
        commit=commit,
        deps=deps,
    )


def write_draft(repo_root: Path, subdir: str, slug: str, payload: dict[str, Any]) -> Path:
    """Write a flow draft deterministically: ``tmp/<subdir>/<slug>.json``.

    Overwrites per-slug so reruns don't accumulate ``<slug>__<run_id>`` files in
    ``tmp/``. Returns the absolute draft path (callers project to repo-relative).
    """
    draft_path = repo_root / "tmp" / subdir / f"{slug}.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return draft_path


def show_thread(slug: str, run_id: str, *, repo_root: Path | None = None, full: bool = False) -> dict[str, Any]:
    """Show the parked (or terminal) state of an existing thread.

    ``repo_root`` is used only as a fallback lesson-display source when
    ``full=True`` and the checkpointed state has no ``lesson`` blob (it should
    always have one in practice -- ``lesson`` is blob-in-state -- but the
    fallback keeps ``show --full`` useful against older/foreign checkpoints).
    """
    config = {"configurable": {"thread_id": thread_id_for(slug, run_id)}}
    with _open_graph() as graph:
        state = graph.get_state(config)
        if not state.values:
            raise ThreadNotFoundError(f"no such thread {thread_id_for(slug, run_id)!r}")
        summary = _summarize_state(graph, config, slug, run_id)
        if full:
            summary["lesson"] = _resolve_lesson_for_display(state.values, slug, repo_root)
            summary["regression_summary"] = _render_regression(state.values.get("regression_result"))
        return summary


def resume_thread(
    slug: str,
    run_id: str,
    decision: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Resume a parked thread with a human decision (accept / edit / defer).

    ``repo_root`` is accepted for call-site symmetry with ``run``/``show`` but
    unused: resume only replays a ``Command(resume=...)`` against the
    checkpointed thread, and every IO path the graph needs (``repo_root``,
    ``output_root``, ``acceptance_log_path``) was already baked into state at
    ``run`` time.
    """
    del repo_root
    config = {"configurable": {"thread_id": thread_id_for(slug, run_id)}}
    with _open_graph() as graph:
        state = graph.get_state(config)
        if not state.values:
            raise ThreadNotFoundError(f"no such thread {thread_id_for(slug, run_id)!r}")
        if K_HUMAN_GATE_NODE not in (state.next or ()):
            raise ThreadNotParkedError(
                f"thread {thread_id_for(slug, run_id)!r} is not parked at the human gate "
                f"(next={list(state.next) if state.next else []!r}); nothing to resume"
            )
        graph.invoke(Command(resume=decision), config=config)
        return _summarize_state(graph, config, slug, run_id)


def list_threads(*, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Enumerate threads in the checkpointer parked at the human gate.

    Walks the checkpointer's raw listing (``checkpointer.list(None)``),
    dedupes by ``thread_id`` (keeping the newest checkpoint per thread, which
    ``list`` already yields first), and filters to threads whose ``.next``
    includes the human gate. This is how an operator discovers run_ids without
    hand-copying a uuid out of ``run`` output.
    """
    del repo_root  # threads are looked up purely from the checkpointer; kept for CLI symmetry
    with _open_graph() as graph:
        checkpointer = graph.checkpointer
        # Materialize the listing before calling get_state(): get_state() reads from
        # the same sqlite connection/cursor that list() is lazily iterating, and
        # interleaving the two over one connection deadlocks.
        checkpoint_configs = [checkpoint_tuple.config for checkpoint_tuple in checkpointer.list(None)]
        seen_thread_ids: set[str] = set()
        rows: list[dict[str, Any]] = []
        for config in checkpoint_configs:
            thread_id = config["configurable"]["thread_id"]
            if thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(thread_id)
            state = graph.get_state(config)
            if K_HUMAN_GATE_NODE not in (state.next or ()):
                continue
            slug, _, run_id = thread_id.partition(":")
            values = state.values
            blocking = [issue for issue in values.get("current_issues", []) if issue.get("is_blocking")]
            rows.append(
                {
                    "slug": slug,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "park_status": values.get("park_status"),
                    "blocking_count": len(blocking),
                    "signoff_score": values.get("signoff_score"),
                }
            )
        return rows


def _summarize_state(graph: Any, config: dict[str, Any], slug: str, run_id: str) -> dict[str, Any]:
    state = graph.get_state(config)
    values = state.values
    return {
        "slug": slug,
        "run_id": run_id,
        "thread_id": thread_id_for(slug, run_id),
        "next": list(state.next) if state.next else [],
        "park_status": values.get("park_status"),
        "ledger_status": values.get("ledger_status"),
        "export_path": values.get("export_path"),
        "blocking_issues": [
            issue.get("message", "")
            for issue in values.get("current_issues", [])
            if issue.get("is_blocking")
        ],
        "signoff_score": values.get("signoff_score"),
        "attempts": values.get("attempts", []),
    }


def _resolve_lesson_for_display(
    values: dict[str, Any], slug: str, repo_root: Path | None
) -> dict[str, Any] | None:
    """Prefer the in-flight lesson from state; fall back to disk only if absent."""
    import json

    lesson = values.get("lesson")
    if lesson is not None:
        return cast("dict[str, Any]", lesson)
    root = Path(repo_root) if repo_root else Path(values.get("output_root") or values.get("repo_root") or K_REPO_ROOT)
    lessons_dir = (root / "data" / "lessons").resolve()
    lesson_path = (lessons_dir / f"{slug}.json")
    if not lesson_path.exists():
        return None
    # Defense-in-depth against path traversal: regardless of caller discipline,
    # the resolved file must stay inside the lessons dir before we read it.
    if not lesson_path.resolve().is_relative_to(lessons_dir):
        raise ValueError(f"lesson slug {slug!r} resolves outside the lessons dir")
    return cast("dict[str, Any]", json.loads(lesson_path.read_text(encoding="utf-8")))


def _render_regression(regression_result: dict[str, Any] | None) -> str:
    """Human-readable one-paragraph summary of the structured regression verdict."""
    if not regression_result:
        return "no regression check has run yet"
    reason = regression_result.get("reason", "unknown")
    if reason == "no_baseline":
        return "no trusted baseline exists for this slug; regression check skipped"
    if reason == "matches_baseline":
        return "matches the trusted baseline exactly; no diff"
    severity = regression_result.get("severity") or "warning"
    diff = regression_result.get("diff") or []
    lines = [f"{len(diff)} non-blocking {severity} diff(s) vs the trusted baseline:"]
    for item in diff:
        lines.append(f"  - {item.get('message', item)}")
    return "\n".join(lines)
