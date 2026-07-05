"""Entry point: `GraphDeps`.

Contracts and default collaborators for the lesson QA graph.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lesson_builder.pipeline.lesson_acceptance_log import latest_regression_baseline
from lesson_builder.pipeline.lesson_persistence import default_exporter
from lesson_builder.pipeline.state import LessonQAState

# Human-gate lifecycle states.
K_PARK_RUNNING = "running"
K_PARK_PARKED = "parked"
K_PARK_DEFERRED = "deferred"
K_PARK_ACCEPTED = "accepted"


@dataclass
class LoadedLesson:
    """Inputs resolved once at entry so downstream nodes stay pure."""

    lesson: dict[str, Any]
    requirements: dict[str, Any] | None = None
    baseline_export: dict[str, Any] | None = None
    recorded_requirements_hash: str | None = None


class LessonLoader(Protocol):
    """Lesson loader."""

    def __call__(self, slug: str, *, repo_root: Path) -> LoadedLesson: ...


class Fixer(Protocol):
    """Author-side collaborator."""

    def __call__(
        self, *, slug: str, lesson: dict[str, Any], issues: list[dict[str, Any]], kind: str
    ) -> dict[str, Any]: ...


class Judge(Protocol):
    """Reviewer-backed judge panel collaborator."""

    def __call__(self, lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]: ...


class Rejudge(Protocol):
    """Focused pedagogy-only re-derivation seam (Task A).

    Re-runs ONLY the pedagogy producer (the one feeding the load-bearing rubric
    floors) after a fix/regenerate changed the lesson, so the floor check re-scores
    the post-fix review instead of the stale pre-fix one. Returns ``None`` when the
    producer fails (the rubric check is then simply skipped for that pass). Only
    invoked when ``load_rubric_floors(...).load_bearing`` is true (cost guard).
    """

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any] | None: ...


class Exporter(Protocol):
    """Exporter. May optionally accept repo-scoped path keywords."""

    def __call__(
        self,
        *,
        slug: str,
        lesson: dict[str, Any],
        run_id: str,
        signoff_score: float | None,
        blocking_issues_messages: list[str],
        corrections_applied: list[str],
        repo_root: Path,
        acceptance_log_path: Path,
        override: bool = False,
        output_root: Path | None = None,
    ) -> str: ...


@dataclass
class GraphDeps:
    """Collaborators injected into the graph so nodes stay pure and testable.

    loader   resolves the slug to a LoadedLesson (lesson + requirements + baseline)
    fixer    author-side fix/regenerate. Required: production callers pass live
             collaborators (codex/real); tests pass ``noop_fixer`` explicitly.
             Never defaulted -- making it required surfaced the test injection
             sites that were silently relying on the noop fallback.
    judge    reviewer-backed advisory panel (pedagogy/objective_alignment/answer).
             Required: same rationale as ``fixer``; tests pass ``noop_judge``.
    rejudge_pedagogy  focused pedagogy-only re-derivation seam (Task A); only
             invoked after a fix/regenerate when rubric floors are load-bearing.
             ``None`` (default) disables re-derivation (offline/test safe).
    exporter terminal export artifact (internal + dist + ledger); default writes
             through ``lesson_export`` and ``lesson_acceptance_log``
    """

    loader: LessonLoader
    fixer: Fixer
    judge: Judge
    rejudge_pedagogy: Rejudge | None = None
    exporter: Exporter = field(default_factory=lambda: default_exporter)


# ---------------------------------------------------------------------------
# Default collaborators
# ---------------------------------------------------------------------------


class LessonLoadError(Exception):
    """A lesson file could not be read or parsed (Task E: typed loader error)."""


def noop_fixer(*, slug: str, lesson: dict[str, Any], issues: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Default author-side no-op."""
    return lesson


def default_loader(slug: str, *, repo_root: Path) -> LoadedLesson:
    """Read ``data/lessons/<slug>.json`` and resolve requirements + baseline.

    Task E: a missing/malformed lesson file surfaces as a typed
    ``LessonLoadError`` (with the slug + underlying cause) instead of a bare
    ``FileNotFoundError``/``json.JSONDecodeError`` that crashes the graph.
    """
    repo_root = Path(repo_root)
    lesson_path = repo_root / "data" / "lessons" / f"{slug}.json"
    lesson = _load_json_lesson(lesson_path, slug=slug, label="lesson")
    requirements_path = repo_root / "data" / "concept_requirements" / f"{slug}.json"
    requirements = (
        _load_json_lesson(requirements_path, slug=slug, label="requirements") if requirements_path.exists() else None
    )
    acceptance_log_path = repo_root / "data" / "lesson_acceptance_log.jsonl"
    baseline_entry = latest_regression_baseline(acceptance_log_path, slug) if acceptance_log_path.exists() else None
    baseline_export: dict[str, Any] | None = None
    if baseline_entry is not None:
        baseline_path = repo_root / baseline_entry.export_path
        if baseline_path.exists():
            baseline_export = _load_json_lesson(baseline_path, slug=slug, label="baseline", missing_ok=True)
    return LoadedLesson(
        lesson=lesson,
        requirements=requirements,
        baseline_export=baseline_export,
        recorded_requirements_hash=baseline_entry.requirements_hash if baseline_entry else None,
    )


def _required_path(state: LessonQAState, field_name: str) -> Path:
    """Resolve a required state path for default loader/exporter collaborators."""
    raw_path = state.get(field_name)
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"LessonQAState[{field_name!r}] is required for the default graph collaborator")
    return Path(raw_path)


def _load_json_lesson(path: Path, *, slug: str, label: str, missing_ok: bool = False) -> dict[str, Any]:
    """Read+parse a JSON lesson artifact with a typed error surface (Task E).

    A missing file raises ``LessonLoadError`` (unless ``missing_ok``), and a
    malformed file raises ``LessonLoadError`` wrapping the underlying
    ``JSONDecodeError``. Never lets a bare exception crash the graph.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if missing_ok:
            return None  # type: ignore[return-value]
        raise LessonLoadError(f"{label} file for slug {slug!r} not found at {path}") from exc
    except OSError as exc:
        raise LessonLoadError(f"{label} file for slug {slug!r} could not be read at {path}: {exc}") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LessonLoadError(f"{label} file for slug {slug!r} at {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LessonLoadError(f"{label} file for slug {slug!r} at {path} is not a JSON object")
    return parsed


__all__ = [
    "GraphDeps",
    "LoadedLesson",
    "LessonLoader",
    "Fixer",
    "Judge",
    "Rejudge",
    "Exporter",
    "default_loader",
    "noop_fixer",
    "LessonLoadError",
    "K_PARK_RUNNING",
    "K_PARK_PARKED",
    "K_PARK_DEFERRED",
    "K_PARK_ACCEPTED",
]
