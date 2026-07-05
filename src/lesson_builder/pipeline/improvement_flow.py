"""Entry point: ``run_improvement_flow``.

Iterative-improvement front-half: parse the request, hydrate retrieval context,
triage ambiguous/off-scope requests, run feasibility checks, author a draft for
supported operations, then feed that draft into the shared Lesson-QA back-half.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.graph_runner import run_back_half, write_draft
from lesson_builder.pipeline.improvement_steps.add_exercises import add_exercises
from lesson_builder.pipeline.improvement_steps.add_explanation import add_explanation
from lesson_builder.pipeline.improvement_steps.feasibility import FeasibilityResult, feasibility_check
from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec, parse_request
from lesson_builder.pipeline.improvement_steps.revise_lesson import revise_lesson
from lesson_builder.pipeline.improvement_steps.triage import TriageResult, triage_request
from lesson_builder.pipeline.lesson_qa_graph import LoadedLesson, default_loader
from lesson_builder.pipeline.store.index import LessonIndex

ImprovementFlowStatus = Literal["parked_for_review", "needs_triage", "needs_human"]

K_IMPROVE_DRAFT_DIR = "improvement_drafts"


def run_improvement_flow(
    text: str,
    *,
    repo_root: Path,
    slug: str | None = None,
    add_exercise: bool = False,
    bloom: str | None = None,
    count: int = 1,
    run_id: str | None = None,
    commit: bool = False,
    author_agent: Any | None = None,
    triage_agent: Any | None = None,
    fixer_name: str = "codex",
    judge_name: str = "real",
    fixer: Any | None = None,
    judge: Any | None = None,
) -> ImprovementFlowResult:
    """Run the deterministic improvement front-half and park the shared graph.

    ``fixer_name``/``judge_name`` default to the live registry defaults (``codex``/
    ``real``) so ``improve`` is judged and repaired just like ``graph run``. Task F:
    pass ``fixer``/``judge`` callables directly to inject offline/test collaborators
    (e.g. ``noop_fixer``/``noop_judge``); ``noop`` is no longer a registry choice.
    """
    repo_root = Path(repo_root)
    idx = LessonIndex(repo_root / "store" / "index.db")
    try:
        lessons_root = repo_root / "data" / "lessons"
        idx.hydrate(lessons_root)
        known_slugs = idx.slugs()
        spec = _build_spec(
            text,
            known_slugs=known_slugs,
            slug=slug,
            add_exercise=add_exercise,
            bloom=bloom,
            count=count,
        )
        lesson = _load_target_lesson(lessons_root, spec.target_slug)
        feasibility = feasibility_check(spec, lesson=lesson, known_slugs=known_slugs)

        if spec.confidence == "low" or feasibility.verdict == "off_scope":
            triage = triage_request(spec, idx, triage_agent=triage_agent)
            return ImprovementFlowResult(
                status="needs_triage",
                spec=spec,
                feasibility=feasibility,
                triage=triage,
                message=triage.rationale,
            )

        if feasibility.verdict == "infeasible":
            return ImprovementFlowResult(
                status="needs_human",
                spec=spec,
                feasibility=feasibility,
                message=feasibility.reason,
            )

        if lesson is None:
            return ImprovementFlowResult(
                status="needs_human",
                spec=spec,
                feasibility=feasibility,
                message="target lesson could not be loaded from data/lessons",
            )

        agent = author_agent or author()
        if spec.operation == "add_exercises":
            draft_lesson = add_exercises(spec, lesson, author_agent=agent)
        elif spec.operation == "add_explanation":
            draft_lesson = add_explanation(spec, lesson, author_agent=agent)
        elif spec.operation == "improve":
            draft_lesson = revise_lesson(spec, lesson, author_agent=agent)

        flow_run_id = run_id or uuid.uuid4().hex[:12]
        draft_path = write_draft(
            repo_root, K_IMPROVE_DRAFT_DIR, spec.target_slug or "unknown", draft_lesson
        )
        graph_result = run_back_half(
            slug=spec.target_slug or "",
            loader=_improve_loader(draft_lesson),
            repo_root=repo_root,
            run_id=flow_run_id,
            commit=commit,
            fixer_name=fixer_name,
            judge_name=judge_name,
            fixer=fixer,
            judge=judge,
        )
        return ImprovementFlowResult(
            status="parked_for_review",
            spec=spec,
            feasibility=feasibility,
            graph=graph_result,
            draft_path=str(draft_path.relative_to(repo_root)),
            message="draft parked at the shared Lesson-QA human gate",
        )
    finally:
        idx.close()


class ImprovementFlowResult(BaseModel):
    """One improvement-flow execution outcome."""

    status: ImprovementFlowStatus
    spec: ImprovementSpec
    feasibility: FeasibilityResult | None = None
    triage: TriageResult | None = None
    graph: dict[str, Any] | None = None
    draft_path: str | None = None
    message: str


def _build_spec(
    text: str,
    *,
    known_slugs: list[str],
    slug: str | None,
    add_exercise: bool,
    bloom: str | None,
    count: int,
) -> ImprovementSpec:
    if add_exercise:
        where = f"the '{slug}' lesson" if slug else "the requested lesson"
        bloom_phrase = f" at the {bloom} bloom level" if bloom else ""
        return ImprovementSpec(
            operation="add_exercises",
            target_slug=slug,
            count=count,
            bloom_level=bloom,
            confidence="high" if slug else "low",
            interpretation_summary=f"Add {count} exercise(s){bloom_phrase} to {where}.",
        )
    spec = parse_request(text, known_slugs=known_slugs)
    if slug:
        spec.target_slug = slug
        spec.confidence = "high"
    return spec


def _load_target_lesson(
    lessons_root: Path,
    target_slug: str | None,
) -> dict[str, Any] | None:
    if not target_slug:
        return None
    lesson_path = lessons_root / f"{target_slug}.json"
    if not lesson_path.exists():
        return None
    return cast("dict[str, Any]", json.loads(lesson_path.read_text(encoding="utf-8")))


def _improve_loader(draft_lesson: dict[str, Any]) -> Any:
    """Build the improve-path loader: reuse ``default_loader`` for requirements +
    baseline, replacing ONLY the lesson with the authored draft.

    Preserves ``loaded.baseline_export`` + ``loaded.recorded_requirements_hash``
    (the regression + stale checks depend on these). Mis-defaulting them to None
    silently disables regression (reviewer trap).
    """

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        loaded = default_loader(slug, repo_root=repo_root)
        return LoadedLesson(
            lesson=draft_lesson,
            requirements=loaded.requirements,
            baseline_export=loaded.baseline_export,
            recorded_requirements_hash=loaded.recorded_requirements_hash,
        )

    return loader


__all__ = [
    "ImprovementFlowResult",
    "ImprovementFlowStatus",
    "run_improvement_flow",
]
