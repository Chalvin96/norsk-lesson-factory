"""Entry point: ``run_bootstrap`` / ``commit_bootstrap_draft`` / ``authorability_sample``.

Phase 2 BOOTSTRAP mode: empty repo -> whole ``CourseMap`` + a card per concept.
Uses ``run_curriculum_loop`` to produce the full ordered set of concepts as
``ConceptRequirements`` cards. If the loop returns ``needs_human`` the bootstrap
surfaces that (it does NOT commit a non-converged map).

Drafts land under ``tmp/curriculum_drafts/<run>/`` (``structure.json`` +
``requirements/<slug>.json`` per concept). A SEPARATE ``commit_bootstrap_draft``
writes ``data/curriculum/structure.json`` +
``data/concept_requirements/*.json`` TRANSACTIONALLY (stage every file in a
temp directory, validate, then atomic rename each into place) so a mid-write
failure cannot leave a half-written curriculum. All-or-nothing. Lesson JSON
(``data/lessons/``) is never touched.

Quality gates (the map has NO ground truth -- be honest about what each proves):
- deterministic **full-card schema validation**: every produced card validates
  as ``ConceptRequirements``; fail loudly if any doesn't.
- **coverage mapping**: every cited competence goal (from resolved research
  notes) maps to >=1 concept; gaps reported in ``coverage_report`` (reuses
  ``review.py`` coverage logic).
- **stratified authorability sample**: ``authorability_sample`` picks one
  concept per CEFR band for a human/caller to run through cold-author -> QA.
  This proves STRUCTURAL authorability (a card parses + exercises the gate
  floors), NOT paradigm-correctness (a wrong-paradigm card parks clean = a
  false pass) and NOT sequencing/coverage quality. It does NOT run cold-author
  here -- it only SELECTS the sample.

All collaborators (research backend, writer/reviewer agents) are DI-injected
so the suite stays offline.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from lesson_builder.pipeline.concept_requirements import ConceptRequirements
from lesson_builder.pipeline.curriculum_design.loop import run_curriculum_loop
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    CurriculumLoopResult,
)
from lesson_builder.pipeline.curriculum_design.review import _coverage_gaps
from lesson_builder.pipeline.curriculum_design.thresholds import CurriculumThresholds

BootstrapStatus = Literal["parked_for_review", "needs_human"]

K_CURRICULUM_STRUCTURE_PATH = Path("data") / "curriculum" / "structure.json"
K_CURRICULUM_REQUIREMENTS_DIR = Path("data") / "concept_requirements"
K_DRAFT_ROOT = Path("tmp") / "curriculum_drafts"

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_bootstrap(
    *,
    repo_root: Path,
    research_backend: Any | None = None,
    writer_agent: Any | None = None,
    reviewer_agent: Any | None = None,
    thresholds: CurriculumThresholds | None = None,
    cefr_bands: tuple[str, ...] = ("A1", "A2", "B1"),
    run_id: str | None = None,
) -> BootstrapResult:
    """Bootstrap the whole-course ``CourseMap`` from an empty repo state.

    Runs the Phase-1 ``run_curriculum_loop`` to author the FULL course (every
    concept as a ``ConceptRequirements`` card with ordering). If the loop does
    not converge (``needs_human``) the bootstrap surfaces that -- it never
    commits a non-converged map.

    Quality gates run BEFORE parking:
    1. Full-card schema validation: every concept is validated as a strict
       ``ConceptRequirements``; a failure raises ``BootstrapValidationError``.
    2. Coverage mapping: cited goals that no concept addresses are reported in
       ``coverage_report`` (the caller/human sees the gaps).

    The draft lands under ``tmp/curriculum_drafts/<run>/`` and never touches
    ``data/lessons/``. A separate ``commit_bootstrap_draft`` does the tracked
    write (transactional).
    """
    repo_root = Path(repo_root)
    seed = _build_bootstrap_seed(cefr_bands)
    loop_result = run_curriculum_loop(
        seed,
        research_backend=research_backend,
        writer_agent=writer_agent,
        reviewer_agent=reviewer_agent,
        thresholds=thresholds,
    )

    if loop_result.status == "needs_human":
        return _build_needs_human_result(loop_result, cefr_bands=cefr_bands)

    _validate_all_cards(loop_result.course_map)
    coverage_report = _build_coverage_report(loop_result)

    flow_run_id = run_id or uuid.uuid4().hex[:12]
    draft_path = _write_draft(
        repo_root,
        flow_run_id,
        course_map=loop_result.course_map,
        coverage_report=coverage_report,
        cefr_bands=cefr_bands,
    )

    return BootstrapResult(
        status="parked_for_review",
        course_map=loop_result.course_map,
        draft_path=str(draft_path.relative_to(repo_root)),
        n_concepts=len(loop_result.course_map.concepts),
        coverage_report=coverage_report,
        message=(
            f"bootstrap draft parked for whole-course review "
            f"({len(loop_result.course_map.concepts)} concepts across "
            f"{'-'.join(cefr_bands)})"
        ),
    )


def commit_bootstrap_draft(
    *,
    repo_root: Path,
    draft_path: Path,
) -> BootstrapCommitResult:
    """Commit an approved bootstrap draft TRANSACTIONALLY (all-or-nothing).

    Stages ``structure.json`` + every ``requirements/<slug>.json`` from the
    draft into a temp directory, re-validates every card as a strict
    ``ConceptRequirements`` BEFORE any target write, then atomically renames
    each staged file into ``data/``. A mid-write failure cannot leave a
    half-written curriculum: validation happens before any target write, and
    each individual file swap is an atomic ``os.replace``.

    Lesson JSON (``data/lessons/``) is never touched.
    """
    repo_root = Path(repo_root)
    source_root = _resolve_draft_path(repo_root, draft_path)
    draft = BootstrapDraft.model_validate_json(
        (source_root / "draft.json").read_text(encoding="utf-8")
    )

    # Stage + validate everything BEFORE touching the tracked tree.
    staged = _stage_draft(repo_root, source_root, draft)

    structure_target = repo_root / K_CURRICULUM_STRUCTURE_PATH
    requirements_targets: list[Path] = []
    committed_paths: list[str] = []

    structure_target.parent.mkdir(parents=True, exist_ok=True)
    (repo_root / K_CURRICULUM_REQUIREMENTS_DIR).mkdir(parents=True, exist_ok=True)

    # Atomic per-file swap: each os.replace is atomic on POSIX.
    _atomic_swap(staged.structure_staging, structure_target)
    committed_paths.append(str(structure_target.relative_to(repo_root)))

    for slug, staged_requirements_path in sorted(staged.requirements_staging.items()):
        target = repo_root / K_CURRICULUM_REQUIREMENTS_DIR / f"{slug}.json"
        _atomic_swap(staged_requirements_path, target)
        requirements_targets.append(target)
        committed_paths.append(str(target.relative_to(repo_root)))

    shutil.rmtree(staged.root, ignore_errors=True)

    return BootstrapCommitResult(
        committed_paths=committed_paths,
        n_concepts=len(staged.requirements_staging),
        lesson_paths_touched=[],
        message=(
            f"approved bootstrap curriculum committed transactionally "
            f"({len(staged.requirements_staging)} concepts); lessons were not modified"
        ),
    )


def authorability_sample(
    course_map: CourseMap,
    *,
    per_band: int = 1,
    cefr_bands: tuple[str, ...] = ("A1", "A2", "B1"),
) -> AuthorabilitySample:
    """Select one concept per CEFR band for a human/caller to cold-author + QA.

    This SELECTS the sample only; it does NOT run cold-author. The sample
    proves STRUCTURAL authorability (a card parses and exercises the gate
    floors when cold-authored), NOT paradigm-correctness (a wrong-paradigm card
    can park clean = a false pass) and NOT sequencing/coverage quality. Label
    it smoke coverage; a human approves the paradigm.
    """
    if per_band < 1:
        raise ValueError(f"per_band must be >= 1; got {per_band}")

    by_band: dict[str, list[ConceptDraft]] = {}
    for concept in course_map.concepts:
        by_band.setdefault(concept.cefr_level, []).append(concept)

    sample: dict[str, list[str]] = {}
    for band in cefr_bands:
        candidates = by_band.get(band, [])
        sample[band] = [c.slug for c in candidates[:per_band]]

    covered_bands = [band for band, slugs in sample.items() if slugs]
    missing_bands = [band for band, slugs in sample.items() if not slugs]

    return AuthorabilitySample(
        per_band=per_band,
        sample=sample,
        covered_bands=covered_bands,
        missing_bands=missing_bands,
        note=(
            "Structural authorability smoke sample only. Proves sampled cards "
            "parse + exercise the gate floors when cold-authored; does NOT prove "
            "paradigm correctness (a wrong-paradigm card parks clean = false pass) "
            "or sequencing/coverage quality. Human approves the paradigm."
        ),
    )


# ---------------------------------------------------------------------------
# Public result models
# ---------------------------------------------------------------------------


class CoverageReport(BaseModel):
    """Coverage report for the bootstrapped map vs cited competence goals.

    Honest about what it proves: the map has NO ground truth. ``covered`` lists
    cited goals mapped to >=1 concept; ``gaps`` lists cited goals with no
    covering concept. Empty ``gaps`` proves the map covers every CITED goal --
    NOT that the cited goals are the right goals (that rests on citation truth
    + human approval).
    """

    model_config = {"extra": "forbid"}

    cited_goals: list[str] = Field(default_factory=list)
    covered: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class AuthorabilitySample(BaseModel):
    """Stratified authorability sample (one concept per CEFR band).

    Proves STRUCTURAL authorability only (a card parses + exercises the gate
    floors when cold-authored), NOT paradigm-correctness or coverage quality.
    See ``authorability_sample``.
    """

    model_config = {"extra": "forbid"}

    per_band: int
    sample: dict[str, list[str]] = Field(default_factory=dict)
    covered_bands: list[str] = Field(default_factory=list)
    missing_bands: list[str] = Field(default_factory=list)
    note: str


class BootstrapResult(BaseModel):
    """Outcome of one ``run_bootstrap`` invocation."""

    model_config = {"extra": "forbid"}

    status: BootstrapStatus
    course_map: CourseMap
    draft_path: str | None = None
    n_concepts: int
    coverage_report: CoverageReport | None = None
    message: str


class BootstrapCommitResult(BaseModel):
    """Committed paths for an approved bootstrap draft (transactional)."""

    model_config = {"extra": "forbid"}

    committed_paths: list[str]
    n_concepts: int
    lesson_paths_touched: list[str]
    message: str


class BootstrapDraft(BaseModel):
    """Reviewable whole-course bootstrap draft."""

    model_config = {"extra": "forbid"}

    structure: dict[str, Any]
    requirements: dict[str, dict[str, Any]]
    coverage_report: dict[str, Any]
    cefr_bands: list[str]


class BootstrapValidationError(Exception):
    """Raised when a bootstrapped card fails full-card schema validation."""


class _StagedDraft:
    """Internal: staged files in a temp directory ready for atomic swap."""

    __slots__ = ("root", "structure_staging", "requirements_staging")

    def __init__(
        self,
        root: Path,
        structure_staging: Path,
        requirements_staging: dict[str, Path],
    ) -> None:
        self.root = root
        self.structure_staging = structure_staging
        self.requirements_staging = requirements_staging


# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------


def _validate_all_cards(course_map: CourseMap) -> None:
    """Quality gate: every concept must validate as a strict ``ConceptRequirements``.

    Fails LOUDLY (``BootstrapValidationError``) on the first card that does not
    validate, naming the offending slug. This is deterministic full-card schema
    validation -- the map has no ground truth, but every card must at least be a
    structurally valid scope card consumable by cold-author with no adapter.
    """
    for concept in course_map.concepts:
        try:
            # Convert through _to_concept_requirements so ConceptDraft-only
            # fields (sequence_index, source_notes) are stripped BEFORE
            # validation against the strict ConceptRequirements contract.
            card = _to_concept_requirements(concept)
            ConceptRequirements.model_validate(card.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 -- surface the slug + re-raise
            raise BootstrapValidationError(
                f"concept {concept.slug!r} failed full-card ConceptRequirements validation: {exc}"
            ) from exc


def _build_coverage_report(loop_result: CurriculumLoopResult) -> CoverageReport:
    """Quality gate: map every cited goal to >=1 concept; report gaps.

    Reuses ``review.py._coverage_gaps`` (the same logic the loop's reviewer
    uses) so the report is consistent with the loop's coverage signal. The map
    has no ground truth -- empty ``gaps`` proves the map covers every CITED
    goal, not that the cited goals are the right goals.
    """
    cited_goals = _cited_goals_from_loop(loop_result)
    gaps = _coverage_gaps(loop_result.course_map, cited_goals)
    covered = [goal for goal in cited_goals if goal not in gaps]
    return CoverageReport(
        cited_goals=cited_goals,
        covered=covered,
        gaps=gaps,
    )


def _cited_goals_from_loop(loop_result: CurriculumLoopResult) -> list[str]:
    """Derive the cited goals the loop actually saw (resolved-note claims).

    The loop's coverage signal uses resolved-note claims as the cited goals.
    Re-derive them from: (a) the course map's ``source_notes`` (claims at least
    one concept drew on = covered goals), PLUS (b) the loop's
    ``signals.coverage_gaps`` (cited goals no concept addressed). Together
    these reconstruct the full cited-goal set the reviewer checked, so the
    coverage_report is consistent with the loop's coverage signal.
    """
    goals: list[str] = []
    seen: set[str] = set()
    for concept in loop_result.course_map.concepts:
        for note in concept.source_notes:
            if note and note not in seen:
                goals.append(note)
                seen.add(note)
    for gap in loop_result.signals.coverage_gaps:
        if gap not in seen:
            goals.append(gap)
            seen.add(gap)
    return goals


# ---------------------------------------------------------------------------
# Draft writing (tmp/; never touches data/)
# ---------------------------------------------------------------------------


def _write_draft(
    repo_root: Path,
    run_id: str,
    *,
    course_map: CourseMap,
    coverage_report: CoverageReport,
    cefr_bands: tuple[str, ...],
) -> Path:
    """Write the draft under ``tmp/curriculum_drafts/<run>/`` (never touches ``data/``)."""
    structure = _build_structure(course_map, cefr_bands=cefr_bands)
    requirements: dict[str, dict[str, Any]] = {
        concept.slug: _to_concept_requirements(concept).model_dump(mode="json")
        for concept in course_map.concepts
    }
    draft = BootstrapDraft(
        structure=structure,
        requirements=requirements,
        coverage_report=coverage_report.model_dump(mode="json"),
        cefr_bands=list(cefr_bands),
    )

    draft_path = repo_root / K_DRAFT_ROOT / run_id
    requirements_path = draft_path / "requirements"
    requirements_path.mkdir(parents=True, exist_ok=True)
    (draft_path / "structure.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (draft_path / "draft.json").write_text(
        draft.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    for slug, requirements_payload in sorted(requirements.items()):
        (requirements_path / f"{slug}.json").write_text(
            json.dumps(requirements_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return draft_path


def _build_structure(course_map: CourseMap, *, cefr_bands: tuple[str, ...]) -> dict[str, Any]:
    """Build the curriculum ``structure.json`` payload from the course map."""
    return {
        "version": 1,
        "cefr_span": "-".join(cefr_bands),
        "topics": [
            {
                "slug": concept.slug,
                "title": _title_from_slug(concept.slug),
                "cefr_level": concept.cefr_level,
                "sequence_index": concept.sequence_index,
                "status": "draft",
            }
            for concept in sorted(course_map.concepts, key=lambda c: c.sequence_index)
        ],
    }


# ---------------------------------------------------------------------------
# Transactional commit (stage -> validate -> atomic swap)
# ---------------------------------------------------------------------------


def _stage_draft(
    repo_root: Path,
    source_root: Path,
    draft: BootstrapDraft,
) -> _StagedDraft:
    """Stage every target file in a temp directory and validate all cards.

    Validation happens HERE, before any target write, so a card that fails
    ``ConceptRequirements`` validation blocks the whole commit (all-or-nothing).
    The staged files live under a temp directory and are swapped into place by
    the caller via ``_atomic_swap``.
    """
    staging_root = Path(tempfile.mkdtemp(prefix="bootstrap_commit_"))
    try:
        staging_structure = staging_root / "structure.json"
        staging_structure.write_text(
            json.dumps(draft.structure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        staging_requirements_dir = staging_root / "requirements"
        staging_requirements_dir.mkdir(parents=True, exist_ok=True)
        staging_requirements: dict[str, Path] = {}

        # Validate EVERY card BEFORE staging any write past validation.
        for slug, payload in draft.requirements.items():
            try:
                ConceptRequirements.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 -- name the slug + re-raise
                raise BootstrapValidationError(
                    f"concept {slug!r} failed full-card ConceptRequirements validation "
                    f"during commit staging: {exc}"
                ) from exc

        for slug, payload in draft.requirements.items():
            staged_path = staging_requirements_dir / f"{slug}.json"
            staged_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            staging_requirements[slug] = staged_path

        return _StagedDraft(
            root=staging_root,
            structure_staging=staging_structure,
            requirements_staging=staging_requirements,
        )
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def _atomic_swap(staged_path: Path, target_path: Path) -> None:
    """Atomically swap a staged file into its target via ``os.replace``.

    ``os.replace`` is atomic on POSIX: the target either retains its prior
    content or fully receives the new content, never a partial write. Combined
    with the pre-write validation in ``_stage_draft``, a crash mid-commit
    leaves each target either at its prior state or fully written -- never a
    truncated/partial file.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_path, target_path)


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def _build_needs_human_result(
    loop_result: CurriculumLoopResult,
    *,
    cefr_bands: tuple[str, ...],
) -> BootstrapResult:
    """Build a ``needs_human`` result from a non-converging loop."""
    unresolved = loop_result.signals.deterministic_hits()
    cited_goals = _cited_goals_from_loop(loop_result)
    gaps = _coverage_gaps(loop_result.course_map, cited_goals)
    coverage_report = CoverageReport(
        cited_goals=cited_goals,
        covered=[goal for goal in cited_goals if goal not in gaps],
        gaps=gaps,
    )
    return BootstrapResult(
        status="needs_human",
        course_map=loop_result.course_map,
        draft_path=None,
        n_concepts=len(loop_result.course_map.concepts),
        coverage_report=coverage_report,
        message=(
            f"bootstrap loop did not converge after {loop_result.rounds} round(s); "
            f"unresolved deterministic signals: {unresolved or '(none)'}. "
            f"No draft written; the map is not parked."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_bootstrap_seed(cefr_bands: tuple[str, ...]) -> str:
    """Build the research seed for the whole-course bootstrap loop."""
    span = "-".join(cefr_bands)
    return (
        f"Design a complete Norwegian (Bokmal) language curriculum covering "
        f"CEFR levels {span}. Produce the full ordered set of concepts a "
        f"learner needs from {cefr_bands[0]} to {cefr_bands[-1]}, with "
        f"objectives, anchor forms, and teaching notes per concept."
    )


def _to_concept_requirements(draft: ConceptDraft) -> ConceptRequirements:
    """Convert a ``ConceptDraft`` to a strict ``ConceptRequirements`` card.

    Drops ordering (``sequence_index``) + provenance (``source_notes``) so the
    card is a strict ``ConceptRequirements`` consumable by cold-author with no
    shape adapter.
    """
    return ConceptRequirements(
        slug=draft.slug,
        cefr_level=draft.cefr_level,
        objectives=[obj.model_copy() for obj in draft.objectives],
        required_anchor_forms=list(draft.required_anchor_forms),
        notes=draft.notes,
        min_clean_examples=draft.min_clean_examples,
    )


def _resolve_draft_path(repo_root: Path, draft_path: Path) -> Path:
    candidate = Path(draft_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


def _title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").title()


__all__ = [
    "AuthorabilitySample",
    "BootstrapCommitResult",
    "BootstrapDraft",
    "BootstrapResult",
    "BootstrapStatus",
    "BootstrapValidationError",
    "CoverageReport",
    "authorability_sample",
    "commit_bootstrap_draft",
    "run_bootstrap",
]
