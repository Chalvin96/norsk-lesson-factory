"""Entry point: ``run_increment`` / ``commit_increment_draft``.

Phase 3 INCREMENT mode: one concept add/split/merge against the existing 104-map.
Subsumes the retired ``curriculum/flow.py``: keeps triage-routing
(``triage_request`` -> patch_lesson/split/new_topic), ``stale_impacts`` (which
existing lessons go stale via ``requirements_hash``), and the tmp-draft ->
human-approve -> commit pattern (writes to ``tmp/curriculum_drafts/``, never
mutates lessons; a commit step copies structure + the new/changed
``concept_requirements`` into ``data/``).

The anchors-only stub (``_generate_requirements``) is REPLACED by the Phase-1
loop: ``run_curriculum_loop`` produces a FULL ``ConceptRequirements`` card
(objectives + bloom + cefr + anchors + notes) for the new/changed concept. If
the loop returns ``needs_human`` the increment surfaces that (it does NOT commit
a non-converged card).

All collaborators (triage agent, research backend, writer/reviewer agents) are
DI-injected so the suite stays offline.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from lesson_builder.pipeline.checks.validators.stale import requirements_hash
from lesson_builder.pipeline.concept_requirements import ConceptRequirements, Objective
from lesson_builder.pipeline.curriculum_design.loop import run_curriculum_loop
from lesson_builder.pipeline.curriculum_design.models import ConceptDraft, CurriculumLoopResult
from lesson_builder.pipeline.lesson_acceptance_log import latest_regression_baseline
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec, parse_request
from lesson_builder.pipeline.nodes.triage import TriageResult, triage_request
from lesson_builder.pipeline.store.index import LessonIndex

IncrementRoute = Literal["patch_lesson", "split", "new_topic"]
IncrementStatus = Literal["parked_for_review", "needs_human", "needs_triage"]

K_CURRICULUM_STRUCTURE_PATH = Path("data") / "curriculum" / "structure.json"
K_CURRICULUM_REQUIREMENTS_DIR = Path("data") / "concept_requirements"
K_ACCEPTANCE_LOG_PATH = Path("data") / "lesson_acceptance_log.jsonl"
K_DRAFT_ROOT = Path("tmp") / "curriculum_drafts"


def run_increment(
    text: str,
    *,
    repo_root: Path,
    slug: str | None = None,
    run_id: str | None = None,
    triage_agent: Any | None = None,
    research_backend: Any | None = None,
    writer_agent: Any | None = None,
    reviewer_agent: Any | None = None,
) -> IncrementResult:
    """Draft one concept change (add/split/merge) and park it for whole-set human review.

    Runs the Phase-1 ``run_curriculum_loop`` to produce a FULL
    ``ConceptRequirements`` card for the target concept. If the loop does not
    converge (``needs_human``) the increment surfaces that — it never commits a
    non-converged card. Drafts land under ``tmp/curriculum_drafts/`` and never
    touch existing lesson JSON.
    """
    repo_root = Path(repo_root)
    idx = LessonIndex(repo_root / "store" / "index.db")
    try:
        lessons_root = repo_root / "data" / "lessons"
        idx.hydrate(lessons_root)
        known_slugs = idx.slugs()

        spec = _parse_curriculum_spec(text, known_slugs=known_slugs, slug=slug)
        triage = triage_request(spec, idx, triage_agent=triage_agent)

        proposed_slug = _resolve_proposed_slug(triage, spec, text)
        if not proposed_slug:
            return IncrementResult(
                status="needs_triage",
                route=triage.proposed_action,
                draft_path=None,
                concept=None,
                stale_impacts=[],
                message=(
                    "could not resolve a target concept slug from the request; "
                    "provide --slug or a more specific topic"
                ),
            )

        seed = _build_loop_seed(text, proposed_slug, repo_root=repo_root)
        loop_result = run_curriculum_loop(
            seed,
            research_backend=research_backend,
            writer_agent=writer_agent,
            reviewer_agent=reviewer_agent,
        )

        concept = _extract_concept(loop_result, proposed_slug=proposed_slug, request_text=text)
        if loop_result.status == "needs_human" or concept is None:
            unresolved = loop_result.signals.deterministic_hits()
            return IncrementResult(
                status="needs_human",
                route=triage.proposed_action,
                draft_path=None,
                concept=concept,
                stale_impacts=[],
                message=(
                    f"curriculum loop did not converge after {loop_result.rounds} round(s); "
                    f"unresolved deterministic signals: {unresolved or '(none)'}"
                ),
            )

        impact = _build_impact(triage, proposed_slug=proposed_slug, repo_root=repo_root)
        stale_impacts = _build_stale_impacts(
            repo_root=repo_root,
            impact=impact,
            concept=concept,
        )

        flow_run_id = run_id or uuid.uuid4().hex[:12]
        draft_path = _write_draft(
            repo_root,
            flow_run_id,
            concept=concept,
            impact=impact,
            stale_impacts=stale_impacts,
            request_text=text,
            known_slugs=known_slugs,
        )

        return IncrementResult(
            status="parked_for_review",
            route=triage.proposed_action,
            draft_path=str(draft_path.relative_to(repo_root)),
            concept=concept,
            stale_impacts=stale_impacts,
            message="curriculum increment draft parked for whole-set review",
        )
    finally:
        idx.close()


def commit_increment_draft(
    *,
    repo_root: Path,
    draft_path: Path,
) -> IncrementCommitResult:
    """Commit an approved increment draft without modifying lesson JSON.

    Copies the draft's ``structure.json`` and each ``requirements/<slug>.json``
    into the tracked ``data/`` tree. Lesson JSON is never touched (an approved
    concept card triggers stale-impact reruns separately).
    """
    repo_root = Path(repo_root)
    source_root = _resolve_draft_path(repo_root, draft_path)
    draft = IncrementDraft.model_validate_json(
        (source_root / "draft.json").read_text(encoding="utf-8")
    )

    structure_path = repo_root / K_CURRICULUM_STRUCTURE_PATH
    requirements_dir = repo_root / K_CURRICULUM_REQUIREMENTS_DIR
    structure_path.parent.mkdir(parents=True, exist_ok=True)
    requirements_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(source_root / "structure.json", structure_path)
    committed_paths = [str(structure_path.relative_to(repo_root))]
    for slug in sorted(draft.requirements):
        source_requirements_path = source_root / "requirements" / f"{slug}.json"
        target_requirements_path = requirements_dir / f"{slug}.json"
        shutil.copyfile(source_requirements_path, target_requirements_path)
        committed_paths.append(str(target_requirements_path.relative_to(repo_root)))

    return IncrementCommitResult(
        committed_paths=committed_paths,
        lesson_paths_touched=[],
        message="approved curriculum increment committed; lessons were not modified",
    )


# ---------------------------------------------------------------------------
# Public result models
# ---------------------------------------------------------------------------


class IncrementImpact(BaseModel):
    """Human-facing impact summary for one curriculum increment request."""

    route: IncrementRoute
    proposed_slug: str
    lesson_exists: bool
    requirements_exists: bool
    affected_lessons: list[str]
    related_lessons: list[str]
    summary: str


class IncrementStaleImpact(BaseModel):
    """Potential lesson staleness caused by a curriculum increment draft."""

    slug: str
    lesson_exists: bool
    requirements_hash_before: str | None
    requirements_hash_after: str | None
    needs_lesson_qa: bool
    reason: str


class IncrementDraft(BaseModel):
    """Reviewable curriculum increment draft."""

    structure: dict[str, Any]
    requirements: dict[str, dict[str, Any]]
    stale_impacts: list[IncrementStaleImpact]


class IncrementResult(BaseModel):
    """Result of one curriculum increment run."""

    status: IncrementStatus
    route: IncrementRoute
    draft_path: str | None = None
    concept: ConceptRequirements | None = None
    stale_impacts: list[IncrementStaleImpact] = Field(default_factory=list)
    message: str


class IncrementCommitResult(BaseModel):
    """Committed paths for an approved curriculum increment draft."""

    committed_paths: list[str]
    lesson_paths_touched: list[str]
    message: str


# ---------------------------------------------------------------------------
# Spec parsing + slug resolution
# ---------------------------------------------------------------------------


def _parse_curriculum_spec(
    text: str,
    *,
    known_slugs: list[str],
    slug: str | None,
) -> ImprovementSpec:
    spec = parse_request(text, known_slugs=known_slugs)
    if slug:
        spec.target_slug = slug
        spec.confidence = "high"
    elif _signals_new_topic(text):
        spec.target_slug = None
        spec.confidence = "low"
    if not spec.target_content:
        spec.target_content = _extract_curriculum_topic(text)
    return spec


def _resolve_proposed_slug(
    triage: TriageResult,
    spec: ImprovementSpec,
    text: str,
) -> str | None:
    """Determine the target slug for the increment, or ``None`` if it cannot be resolved."""
    if spec.target_slug:
        return spec.target_slug
    evidence_slugs = [item.slug for item in triage.evidence if item.slug]
    if evidence_slugs:
        return evidence_slugs[0]
    content = spec.target_content or _extract_curriculum_topic(text)
    if content and content.strip():
        return _slugify(content)
    return None


def _build_loop_seed(text: str, proposed_slug: str, *, repo_root: Path) -> str:
    """Build the research seed for the Phase-1 loop.

    Incorporates the concept slug, the request text, and (for patch_lesson)
    the existing card's notes + anchors so the research/writer passes have
    context for the concept being changed.
    """
    existing = _load_existing_requirements(repo_root, proposed_slug)
    title = proposed_slug.replace("_", " ")
    parts = [f"Concept: {title}", f"Request: {text}"]
    if existing is not None:
        parts.append(f"Existing notes: {existing.notes}")
        if existing.required_anchor_forms:
            parts.append(f"Existing anchors: {', '.join(existing.required_anchor_forms)}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Loop concept extraction
# ---------------------------------------------------------------------------


def _extract_concept(
    loop_result: CurriculumLoopResult,
    *,
    proposed_slug: str,
    request_text: str,
) -> ConceptRequirements | None:
    """Extract the target concept from the loop's ``CourseMap`` as a ``ConceptRequirements`` card.

    Prefers an exact slug match; falls back to the first concept. Overrides the
    slug to ``proposed_slug`` (the increment target) when they differ. Appends
    the increment request context to ``notes`` so the human reviewer can see
    what prompted the change.
    """
    concepts = loop_result.course_map.concepts
    if not concepts:
        return None

    match = next((c for c in concepts if c.slug == proposed_slug), None)
    draft = match if match is not None else concepts[0]
    return _to_concept_requirements(draft, proposed_slug=proposed_slug, request_text=request_text)


def _to_concept_requirements(
    draft: ConceptDraft,
    *,
    proposed_slug: str,
    request_text: str,
) -> ConceptRequirements:
    """Convert a ``ConceptDraft`` to a ``ConceptRequirements`` card.

    Drops ordering (``sequence_index``) + provenance (``source_notes``) so the
    card is a strict ``ConceptRequirements`` consumable by cold-author with no
    shape adapter. The slug is pinned to the increment target.
    """
    notes = _append_note(draft.notes, f"Curriculum increment request: {request_text}")
    objectives: list[Objective] = [obj.model_copy() for obj in draft.objectives]
    return ConceptRequirements(
        slug=proposed_slug or draft.slug,
        cefr_level=draft.cefr_level,
        objectives=objectives,
        required_anchor_forms=list(draft.required_anchor_forms),
        notes=notes,
        min_clean_examples=draft.min_clean_examples,
    )


# ---------------------------------------------------------------------------
# Impact + stale computation (mirrors the retired flow.py discipline)
# ---------------------------------------------------------------------------


def _build_impact(
    triage: TriageResult,
    *,
    proposed_slug: str,
    repo_root: Path,
) -> IncrementImpact:
    evidence_slugs = list(dict.fromkeys(item.slug for item in triage.evidence))
    lesson_exists = (repo_root / "data" / "lessons" / f"{proposed_slug}.json").exists()
    requirements_exists = _existing_requirements_path(repo_root, proposed_slug) is not None

    if triage.proposed_action == "patch_lesson":
        affected_lessons = [triage.spec.target_slug or proposed_slug]
        related_lessons = evidence_slugs
        summary = (
            f"request fits existing lesson {affected_lessons[0]}; "
            "patch curriculum first, then re-run lesson QA"
        )
    elif triage.proposed_action == "split":
        affected_lessons = evidence_slugs[:3]
        related_lessons = evidence_slugs
        summary = "request overlaps existing lessons; review whether to split, redirect, or merge"
    else:
        affected_lessons = []
        related_lessons = evidence_slugs
        summary = (
            f"request looks like a new topic; review generated requirements for {proposed_slug}"
        )

    return IncrementImpact(
        route=triage.proposed_action,
        proposed_slug=proposed_slug,
        lesson_exists=lesson_exists,
        requirements_exists=requirements_exists,
        affected_lessons=affected_lessons,
        related_lessons=related_lessons,
        summary=summary,
    )


def _build_stale_impacts(
    *,
    repo_root: Path,
    impact: IncrementImpact,
    concept: ConceptRequirements,
) -> list[IncrementStaleImpact]:
    impacted_slugs = list(
        dict.fromkeys(
            [impact.proposed_slug, *impact.affected_lessons, *impact.related_lessons]
        )
    )
    stale_impacts: list[IncrementStaleImpact] = []
    for slug in impacted_slugs:
        lesson_exists = (repo_root / "data" / "lessons" / f"{slug}.json").exists()
        if not lesson_exists:
            continue
        next_requirements = (
            concept.model_dump(mode="json")
            if slug == impact.proposed_slug
            else _load_requirements(repo_root, slug)
        )
        requirements_hash_after = (
            requirements_hash(next_requirements) if next_requirements else None
        )
        baseline = latest_regression_baseline(repo_root / K_ACCEPTANCE_LOG_PATH, slug)
        requirements_hash_before = baseline.requirements_hash if baseline else None
        needs_lesson_qa = (
            requirements_hash_after is not None
            and requirements_hash_before is not None
            and requirements_hash_after != requirements_hash_before
        )
        if requirements_hash_before is None and requirements_hash_after is not None:
            needs_lesson_qa = True
        stale_impacts.append(
            IncrementStaleImpact(
                slug=slug,
                lesson_exists=lesson_exists,
                requirements_hash_before=requirements_hash_before,
                requirements_hash_after=requirements_hash_after,
                needs_lesson_qa=needs_lesson_qa,
                reason=(
                    "curriculum requirements changed; rerun the lesson through Lesson-QA"
                    if needs_lesson_qa
                    else "no requirements change detected from the current draft"
                ),
            )
        )
    return stale_impacts


# ---------------------------------------------------------------------------
# Draft writing
# ---------------------------------------------------------------------------


def _write_draft(
    repo_root: Path,
    run_id: str,
    *,
    concept: ConceptRequirements,
    impact: IncrementImpact,
    stale_impacts: list[IncrementStaleImpact],
    request_text: str,
    known_slugs: list[str],
) -> Path:
    structure = _load_or_seed_structure(repo_root, known_slugs)
    requirements: dict[str, dict[str, Any]] = {}

    if impact.route == "patch_lesson" and impact.proposed_slug:
        requirements[impact.proposed_slug] = concept.model_dump(mode="json")
    else:
        _append_topic(structure, impact.proposed_slug, request_text=request_text)
        requirements[impact.proposed_slug] = concept.model_dump(mode="json")

    draft = IncrementDraft(
        structure=structure,
        requirements=requirements,
        stale_impacts=stale_impacts,
    )

    draft_path = repo_root / K_DRAFT_ROOT / run_id
    requirements_path = draft_path / "requirements"
    requirements_path.mkdir(parents=True, exist_ok=True)
    (draft_path / "structure.json").write_text(
        json.dumps(draft.structure, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (draft_path / "draft.json").write_text(
        draft.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    for slug, requirements_payload in sorted(draft.requirements.items()):
        (requirements_path / f"{slug}.json").write_text(
            json.dumps(requirements_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return draft_path


def _load_or_seed_structure(repo_root: Path, known_slugs: list[str]) -> dict[str, Any]:
    structure_path = repo_root / K_CURRICULUM_STRUCTURE_PATH
    if structure_path.exists():
        return cast("dict[str, Any]", json.loads(structure_path.read_text(encoding="utf-8")))
    return {
        "version": 1,
        "topics": [
            {
                "slug": lesson_slug,
                "title": _title_from_slug(lesson_slug),
                "status": "existing",
            }
            for lesson_slug in known_slugs
        ],
    }


def _append_topic(structure: dict[str, Any], slug: str, *, request_text: str) -> None:
    topics = structure.setdefault("topics", [])
    if any(topic.get("slug") == slug for topic in topics):
        return
    topics.append(
        {
            "slug": slug,
            "title": _title_from_slug(slug),
            "status": "draft",
            "source_request": request_text,
        }
    )


# ---------------------------------------------------------------------------
# Requirements loading helpers
# ---------------------------------------------------------------------------


def _load_existing_requirements(repo_root: Path, slug: str) -> ConceptRequirements | None:
    requirements_path = _existing_requirements_path(repo_root, slug)
    if requirements_path is None:
        return None
    payload = json.loads(requirements_path.read_text(encoding="utf-8"))
    return ConceptRequirements.model_validate(payload)


def _load_requirements(repo_root: Path, slug: str) -> dict[str, Any] | None:
    requirements_path = _existing_requirements_path(repo_root, slug)
    if requirements_path is None:
        return None
    return cast("dict[str, Any]", json.loads(requirements_path.read_text(encoding="utf-8")))


def _existing_requirements_path(repo_root: Path, slug: str) -> Path | None:
    requirements_path = repo_root / K_CURRICULUM_REQUIREMENTS_DIR / f"{slug}.json"
    return requirements_path if requirements_path.exists() else None


def _resolve_draft_path(repo_root: Path, draft_path: Path) -> Path:
    candidate = Path(draft_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _extract_curriculum_topic(text: str) -> str:
    match = re.search(r"(?:topic|lesson|unit)\s+(?:on|about)\s+(.+)$", text.lower().strip())
    if match:
        return match.group(1).strip()
    return text.strip().lower()


def _signals_new_topic(text: str) -> bool:
    lowered = text.lower()
    return "new topic" in lowered or "new lesson" in lowered or "new unit" in lowered


def _slugify(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return normalized or "new_topic"


def _title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").title()


def _append_note(existing_note: str, note: str) -> str:
    if not existing_note:
        return note
    return f"{existing_note} {note}"


__all__ = [
    "IncrementCommitResult",
    "IncrementDraft",
    "IncrementImpact",
    "IncrementResult",
    "IncrementRoute",
    "IncrementStaleImpact",
    "IncrementStatus",
    "commit_increment_draft",
    "run_increment",
]
