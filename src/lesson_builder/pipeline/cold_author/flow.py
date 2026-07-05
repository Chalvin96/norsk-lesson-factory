"""Entry point: ``cold_author_flow``.

The Journey 1 (J1) entry point: produce a draft ``Lesson`` from
``data/concept_requirements/<slug>.json`` (cold authoring, no existing lesson),
then feed it into the shared Lesson-QA back-half so a cold lesson gets the same
gating as an improved one. Parks at the same human gate and exports through the
same sink.

Stages (each a separate LLM call):

1. ``author_metadata_objectives`` -- title + goal, merged with card-supplied CEFR/objectives.
2. ``author_sections`` -- teaching sections covering every objective.
3. ``author_exercises`` -- >= 2 valid exercises per objective.
4. ``assemble_lesson`` -- deterministic assembly + anchor injection + preflight.

C1: a STANDALONE draft loader is used (NOT ``default_loader``, which would raise
``FileNotFoundError`` on a cold slug). The loader hands the assembled draft +
the requirements read from disk to ``run_graph``; ``baseline_export`` and the
recorded hash are None so the regression node and stale check no-op.

R3: refuse when ``data/lessons/<slug>.json`` already exists unless ``force=True``
(cold-author is for slugs with requirements but no lesson; use ``improve``
otherwise).

R4: a mid-chain failure (backend down or unrepairable stage output) short-
circuits to a ``needs_human`` result and preserves the earlier stage output as a
draft on disk so the work is not lost.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.cold_author.assemble import assemble_lesson
from lesson_builder.pipeline.cold_author.exercises import author_exercises
from lesson_builder.pipeline.cold_author.metadata_objectives import author_metadata_objectives
from lesson_builder.pipeline.cold_author.models import (
    AssemblyError,
    ColdAuthorResult,
    StageFailure,
)
from lesson_builder.pipeline.cold_author.sections import author_sections
from lesson_builder.pipeline.concept_requirements import (
    ConceptRequirements,
    load_concept_requirements,
)
from lesson_builder.pipeline.graph_runner import run_back_half, write_draft
from lesson_builder.pipeline.lesson_qa_graph import LoadedLesson

K_COLD_DRAFT_DIR = "cold_author_drafts"


def cold_author_flow(
    slug: str,
    *,
    repo_root: Path,
    commit: bool = False,
    run_id: str | None = None,
    author_agent: Any | None = None,
    fixer_name: str = "codex",
    judge_name: str = "real",
    fixer: Any | None = None,
    judge: Any | None = None,
    force: bool = False,
) -> ColdAuthorResult:
    """Run cold authoring for ``slug`` and park the draft at the human gate.

    Task F: ``fixer``/``judge`` accept injectable callables directly (tests pass
    ``noop_fixer``/``noop_judge`` here); ``noop`` is no longer a selectable
    registry option. When ``fixer``/``judge`` are ``None``, the named registry
    entries are used (CLI defaults: codex/real).
    """
    repo_root = Path(repo_root)
    flow_run_id = run_id or uuid.uuid4().hex[:12]
    agent = author_agent or author()

    refusal = _check_overwrite_guard(repo_root, slug, force=force)
    if refusal is not None:
        return refusal

    requirements = _load_requirements(repo_root, slug)
    if requirements is None:
        return ColdAuthorResult(
            status="needs_human",
            slug=slug,
            run_id=flow_run_id,
            message=(
                f"no requirements file found at data/concept_requirements/{slug}.json; "
                "run the curriculum flow first"
            ),
        )

    metadata_stage = author_metadata_objectives(slug, requirements, author_agent=agent)
    if isinstance(metadata_stage, StageFailure):
        return _needs_human_from_failure(
            repo_root, slug, flow_run_id, metadata_stage, partial=None
        )
    metadata = metadata_stage.payload.model_dump(mode="json")
    requirements_payload = requirements.model_dump(mode="json")

    sections_stage = author_sections(
        metadata["objectives"], requirements_payload, author_agent=agent
    )
    if isinstance(sections_stage, StageFailure):
        return _needs_human_from_failure(
            repo_root, slug, flow_run_id, sections_stage, partial={"metadata": metadata}
        )

    exercises_stage = author_exercises(
        metadata["objectives"], sections=sections_stage.payload, author_agent=agent
    )
    if isinstance(exercises_stage, StageFailure):
        return _needs_human_from_failure(
            repo_root,
            slug,
            flow_run_id,
            exercises_stage,
            partial={"metadata": metadata, "sections": sections_stage.payload},
        )

    try:
        draft = assemble_lesson(
            slug,
            requirements_payload,
            metadata,
            sections_stage.payload,
            exercises_stage.payload,
        )
    except AssemblyError as exc:
        failure = StageFailure("assemble", str(exc))
        return _needs_human_from_failure(
            repo_root,
            slug,
            flow_run_id,
            failure,
            partial={
                "metadata": metadata,
                "sections": sections_stage.payload,
                "exercises": exercises_stage.payload,
            },
        )

    draft_path = write_draft(repo_root, K_COLD_DRAFT_DIR, slug, draft)
    graph_result = run_back_half(
        slug=slug,
        loader=_cold_loader(draft, requirements),
        repo_root=repo_root,
        run_id=flow_run_id,
        commit=commit,
        fixer_name=fixer_name,
        judge_name=judge_name,
        fixer=fixer,
        judge=judge,
    )
    return ColdAuthorResult(
        status="parked_for_review",
        slug=slug,
        run_id=flow_run_id,
        message="cold-authored draft parked at the shared Lesson-QA human gate",
        draft_path=str(draft_path.relative_to(repo_root)),
        graph=graph_result,
    )


def _check_overwrite_guard(
    repo_root: Path, slug: str, *, force: bool
) -> ColdAuthorResult | None:
    if force:
        return None
    existing = repo_root / "data" / "lessons" / f"{slug}.json"
    if existing.exists():
        return ColdAuthorResult(
            status="refused",
            slug=slug,
            message=(
                f"data/lessons/{slug}.json already exists; use 'lesson-data improve --slug "
                f"{slug}' to iterate on an existing lesson, or pass --force to cold-author over it"
            ),
        )
    return None


def _load_requirements(repo_root: Path, slug: str) -> ConceptRequirements | None:
    path = repo_root / "data" / "concept_requirements" / f"{slug}.json"
    if not path.exists():
        return None
    return load_concept_requirements(path)


def _needs_human_from_failure(
    repo_root: Path,
    slug: str,
    run_id: str,
    failure: StageFailure,
    *,
    partial: dict[str, Any] | None,
) -> ColdAuthorResult:
    draft_path = write_draft(
        repo_root,
        K_COLD_DRAFT_DIR,
        slug,
        {"slug": slug, "failed_stage": failure.stage, "reason": failure.reason, **(partial or {})},
    )
    return ColdAuthorResult(
        status="needs_human",
        slug=slug,
        run_id=run_id,
        message=f"cold-author stage '{failure.stage}' failed: {failure.reason}",
        draft_path=str(draft_path.relative_to(repo_root)),
        failed_stage=failure.stage,
        stage_reason=failure.reason,
    )


def _cold_loader(
    draft_lesson: dict[str, Any],
    requirements: ConceptRequirements,
) -> Any:
    """Build the cold-author loader: standalone (NOT default_loader, which raises
    FileNotFoundError on a cold slug). Returns the draft with the requirements
    read from disk; ``baseline_export`` and ``recorded_requirements_hash`` are
    None so the regression node and stale check no-op.
    """

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:  # noqa: ARG001
        del slug, repo_root
        return LoadedLesson(
            lesson=draft_lesson,
            requirements=requirements.model_dump(mode="json"),
            baseline_export=None,
            recorded_requirements_hash=None,
        )

    return loader


__all__ = ["cold_author_flow"]
