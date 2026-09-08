"""Entry points: ``resolve_candidates_node`` and ``build_resolve_candidates_prompt``.

The stage owns semantic resolution and deterministic resolution validation,
together with the prompt for its catalog-review call.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.catalog.services.normalization import normalize_text
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.models import ResolutionBatch
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import error_message
from lesson_builder.workflow.catalog_design.nodes.node_support import existing_lessons
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_MERGE_GENERIC_TOKENS
from lesson_builder.workflow.catalog_design.state import CatalogState

K_CATALOG_MERGE_MIN_CANDIDATES = 2

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def resolve_candidates_node(state: CatalogState, deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Ask catalog review to merge, split, relate, or reject candidates."""
    request = request_from_state(state)
    candidates = candidate_list(state.get("filtered_candidates", []))
    try:
        result = ResolutionBatch.model_validate(
            deps.resolve(request, candidates, existing_lessons(state), state.get("advice"))
        )
    except Exception as exc:  # noqa: BLE001 - structured failure is part of proposal status
        return {"resolutions": [], "errors": [error_message("Catalog review resolution", exc)]}
    resolutions, errors = _valid_resolutions(
        result.resolutions,
        candidates,
        existing_lessons(state),
    )
    return {
        "resolutions": [resolution.model_dump(mode="json") for resolution in resolutions],
        "architecture_question": result.architecture_question,
        "architecture_question_reason": result.architecture_question_reason,
        "errors": errors,
    }


def build_resolve_candidates_prompt(
    request: CatalogRequest,
    advice: str | None,
    *,
    existing_lessons_payload: str,
    candidates_payload: str,
) -> str:
    """Build the catalog-review semantic resolution prompt."""
    return f"""
You are the offline semantic adjudicator in a Norwegian catalog pipeline.
Resolve the candidate set below against the existing lesson snapshot as catalog
owners.

Category: {request.category}
Category guidance: {request.category_guidance or "No extra guidance."}
Prior catalog-review advice, if any: {advice or "none"}

Compare every candidate with the full catalog and require an independent learner
decision for novelty; context, audience, setting, examples, narrower wording,
or a new title alone preserve the existing owner. Use these relationship labels:
exact_duplicate, semantic_duplicate, merge_candidate, lesson_extension, new, or uncertain. Keep
the legacy labels distinct/merge/duplicate/broader/narrower/related accepted for
compatibility. A new entry must state why it changes what a learner can decide
or do. A merge candidate must name its supported, non-redundant addition. An
uncertain entry must contain one narrow semantic or pragmatic review question.

When a candidate adds one teaching point within an existing owner's learner decision, use
`lesson_extension`, set `existing_slug` to the owning lesson, and state the new
atomic learner decision in `teaching_point`. Do not create a second catalog
owner for that addition. Use duplicate relationships when the candidate merely
restates an existing objective.

Use a stable canonical slug and title for each resolution. Include up to three nearest existing
comparisons with shared_core and independent_difference. Flag architecture_question
when the category boundary, merge policy, or candidate scope requires a human
decision. Rewrite each canonical title when needed so it follows the same
short, active, ASD-STE100-style learner-facing title rule. Do not use internal
taxonomy labels as the title.

Completion criterion: include every input candidate exactly once across the
resolutions and give every resolution one relationship, rationale, confidence,
and canonical identity.

Existing snapshot:
{existing_lessons_payload}

Candidates:
{candidates_payload}
"""


def _valid_resolutions(
    resolutions: Iterable[CandidateResolution],
    candidates: list[CatalogCandidate],
    existing_lessons: list[ExistingLesson],
) -> tuple[list[CandidateResolution], list[str]]:
    known = {candidate.candidate_id for candidate in candidates}
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    existing_slug_by_key = {
        normalize_slug(value): lesson.slug
        for lesson in existing_lessons
        for value in (lesson.slug, lesson.title, *lesson.aliases)
        if normalize_slug(value)
    }
    assigned: set[str] = set()
    valid: list[CandidateResolution] = []
    errors: list[str] = []
    for resolution in resolutions:
        unknown = [item for item in resolution.candidate_ids if item not in known]
        duplicate = [item for item in resolution.candidate_ids if item in assigned]
        if unknown or duplicate:
            errors.append(
                f"Catalog review references unknown candidates={unknown!r} or already assigned candidates={duplicate!r}"
            )
            continue
        existing_slug = (
            existing_slug_by_key.get(normalize_slug(resolution.existing_slug)) if resolution.existing_slug else None
        )
        if resolution.relationship == "lesson_extension" and not existing_slug:
            errors.append(f"lesson_extension references an unknown existing lesson {resolution.existing_slug!r}")
            continue
        if resolution.relationship == "lesson_extension" and not resolution.teaching_point.strip():
            errors.append("lesson_extension must include a concise teaching_point statement")
            continue
        if resolution.relationship == "lesson_extension" and existing_slug != resolution.existing_slug:
            resolution = resolution.model_copy(update={"existing_slug": existing_slug})
        if resolution.relationship in {"merge", "merge_candidate"}:
            group = [candidate_by_id[item] for item in resolution.candidate_ids]
            if not _merge_cores_overlap(group):
                valid.append(
                    resolution.model_copy(
                        update={
                            "relationship": "uncertain",
                            "review_question": (
                                "The proposed merge has disjoint teachable cores. "
                                "Review whether these owners must remain separate."
                            ),
                        }
                    )
                )
                assigned.update(resolution.candidate_ids)
                continue
        assigned.update(resolution.candidate_ids)
        valid.append(resolution)
    missing = sorted(known - assigned)
    if missing:
        errors.append(f"Catalog review left candidates unresolved: {missing!r}")
    return valid, errors


def _merge_cores_overlap(candidates: list[CatalogCandidate]) -> bool:
    """Return whether a proposed merge shares a meaningful teachable token."""
    if len(candidates) < K_CATALOG_MERGE_MIN_CANDIDATES:
        return True
    token_sets = [
        set(normalize_text(candidate.teachable_core).split()) - set(K_CATALOG_MERGE_GENERIC_TOKENS)
        for candidate in candidates
    ]
    shared = set.intersection(*token_sets) if token_sets else set()
    return bool(shared)


__all__ = ["build_resolve_candidates_prompt", "resolve_candidates_node"]
