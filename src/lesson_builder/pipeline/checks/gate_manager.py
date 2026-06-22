"""Entry point: ``gate_lesson_results``.

``gate_lesson_results`` runs the deterministic validators and returns the raw
``list[CheckResult]`` the graph's checks node consumes (per-issue granularity for
routing/audit). ``gate_advisory_results`` is the sibling composition point: it folds
the LLM judge review payloads into ``list[CheckResult]``. Issue-list findings stay
advisory; the promoted rubric-floor LLM layer routes lessons back through revision
without becoming a hard final-export blocker. The graph spine reads
``is_blocking``/``revision_target`` off these results directly — there is no separate
summary/manifest vocabulary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from lesson_builder.pipeline.calibration.rubric_floors import (
    RubricFloors,
    load_rubric_floors,
    rubric_floor_check,
    rubric_floors_path_for_repo,
)
from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.checks.validators.answer_valid import answer_valid_check
from lesson_builder.pipeline.checks.validators.bloom_alignment import bloom_alignment_check
from lesson_builder.pipeline.checks.validators.naturalness import naturalness_check_from_review
from lesson_builder.pipeline.checks.validators.nynorsk import nynorsk_check
from lesson_builder.pipeline.checks.validators.objective_alignment import (
    objective_alignment_check_from_review,
)
from lesson_builder.pipeline.checks.validators.objective_structural import objective_structural_check
from lesson_builder.pipeline.checks.validators.pedagogy import pedagogy_check_from_review
from lesson_builder.pipeline.checks.validators.regression import regression_check
from lesson_builder.pipeline.checks.validators.requirement_anchors import requirement_anchors_check
from lesson_builder.pipeline.checks.validators.schema_validate import schema_validate
from lesson_builder.pipeline.checks.validators.stale import stale_check
from lesson_builder.pipeline.checks.validators.structural import exercise_structural_checks
from lesson_builder.pipeline.checks.validators.terminology import terminology_check

K_GATE_REQUIREMENTS_NOT_PROVIDED = object()


def gate_lesson_results(
    lesson: dict[str, Any],
    *,
    requirements: dict[str, Any] | None | object = K_GATE_REQUIREMENTS_NOT_PROVIDED,
    baseline_export: dict[str, Any] | None = None,
    recorded_requirements_hash: str | None = None,
) -> list[CheckResult]:
    """Run the gate validators and return the raw per-issue CheckResult list.

    The graph's checks node reads this for routing (blocking) and audit. Schema
    failure short-circuits (returns only the schema results).
    """
    slug = _resolve_slug(lesson)
    resolved_requirements = _resolve_requirements(slug, requirements)
    schema_results = schema_validate(lesson)
    if schema_results:
        return list(schema_results)
    results: list[CheckResult] = []
    results.extend(schema_results)
    results.extend(exercise_structural_checks(lesson))
    results.extend(objective_structural_check(lesson))
    results.extend(bloom_alignment_check(lesson))
    results.extend(nynorsk_check(lesson))
    results.extend(requirement_anchors_check(lesson, resolved_requirements))
    results.extend(regression_check(slug, lesson, baseline_export))
    results.extend(stale_check(slug, resolved_requirements, recorded_requirements_hash))
    results.extend(terminology_check(lesson))
    return results


def gate_advisory_results(
    lesson: dict[str, Any],
    *,
    pedagogy_review: dict[str, Any] | None = None,
    objective_alignment_review: dict[str, Any] | None = None,
    answer_review: dict[str, Any] | None = None,
    naturalness_review: dict[str, Any] | None = None,
    repo_root: str | Path | None = None,
) -> list[CheckResult]:
    """Fold advisory (LLM judge) review payloads into ``list[CheckResult]``.

    The advisory counterpart to the deterministic ``gate_lesson_results``. Two
    kinds of finding come out of the reviewer:

    * **Issue lists** (pedagogy / objective_alignment / answer / naturalness)
      stay ADVISORY (log-only) — calibration showed they over-flag
      (recall ~1.0, precision ~0.25), so they are never promoted to load-bearing.
    * **Rubric floors** — the reviewer's 7-axis pedagogy SCORES are stable and
      calibratable; ``rubric_floor_check`` marks a lesson scoring below the
      golden-derived floor on any axis as a revision target once the floors are
      promoted (``rubric_floors.json`` ``load_bearing: true``). This is the
      routed LLM layer.

    Takes plain keyword params rather than the graph state TypedDict so this
    module stays independent of ``lesson_qa_graph`` / ``state``.
    """
    results: list[CheckResult] = []
    if pedagogy_review:
        pedagogy_results, pedagogy_model = pedagogy_check_from_review(pedagogy_review)
        results.extend(pedagogy_results)  # advisory issue findings
        floors = _load_repo_rubric_floors(repo_root)
        results.extend(rubric_floor_check(pedagogy_model, floors))  # routed-to-revision when promoted
    if objective_alignment_review:
        alignment_results, _ = objective_alignment_check_from_review(objective_alignment_review)
        results.extend(alignment_results)
    if answer_review:
        answer_results, _ = answer_valid_check(lesson, answer_review)
        results.extend(answer_results)
    if naturalness_review:
        naturalness_results, _ = naturalness_check_from_review(naturalness_review)
        results.extend(naturalness_results)
    return results


def _resolve_slug(lesson: dict[str, Any]) -> str:
    return str(lesson.get("concept_slug") or lesson.get("key") or "unknown")


def _resolve_requirements(slug: str, requirements: dict[str, Any] | None | object) -> dict[str, Any] | None:
    if requirements is K_GATE_REQUIREMENTS_NOT_PROVIDED:
        return _load_requirements_from_disk(slug)
    return requirements if isinstance(requirements, dict) else None


def _load_requirements_from_disk(slug: str) -> dict[str, Any] | None:
    """Resolve ``data/concept_requirements/<slug>.json`` when it exists."""
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / "data" / "concept_requirements" / f"{slug}.json"
    if path.exists():
        return cast("dict[str, Any]", json.loads(path.read_text()))
    return None


def _load_repo_rubric_floors(repo_root: str | Path | None) -> RubricFloors:
    """Resolve the rubric floors for ``repo_root`` (defaults if unset)."""
    if not repo_root:
        return load_rubric_floors()
    return load_rubric_floors(rubric_floors_path_for_repo(Path(repo_root)))
