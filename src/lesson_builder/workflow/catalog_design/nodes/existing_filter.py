"""Entry point: ``existing_filter_node`` (registered as ``existing_filter``).

``build_catalog_graph`` calls this node to reject exact existing or invalid
candidates with deterministic policy before any expensive semantic call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.catalog.services.normalization import normalize_text
from lesson_builder.workflow.catalog_design.context import match_existing_lesson
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import existing_lessons
from lesson_builder.workflow.catalog_design.nodes.node_support import rejection
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_TITLE_BANNED_TERMS
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def existing_filter_node(state: CatalogState, _deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Reject exact existing/invalid candidates before expensive semantic calls."""
    request = request_from_state(state)
    existing_lesson_records = existing_lessons(state)
    remaining: list[CatalogCandidate] = []
    rejections: list[dict[str, Any]] = []
    candidates = candidate_list(state.get("candidates", []))
    reference_keys = _known_reference_keys(existing_lesson_records, candidates)
    for candidate in candidates:
        shape_error = _candidate_shape_error(candidate, reference_keys)
        if shape_error is not None:
            rejections.append(
                rejection(
                    candidate,
                    title=candidate.title or candidate.slug,
                    status="rejected_invalid",
                    relationship=None,
                    reason=shape_error,
                    confidence=1.0,
                ).model_dump(mode="json")
            )
            continue
        existing = match_existing_lesson(candidate, existing_lesson_records)
        if existing is not None:
            rejections.append(
                rejection(
                    candidate,
                    title=existing.title,
                    status="rejected_existing",
                    relationship="duplicate",
                    reason=f"exact slug, title, or alias match for existing lesson {existing.slug}",
                    confidence=1.0,
                ).model_dump(mode="json")
            )
            continue
        if not _categories_match(candidate.category, request.category):
            rejections.append(
                rejection(
                    candidate,
                    title=candidate.title,
                    status="rejected_invalid",
                    relationship=None,
                    reason=f"candidate category {candidate.category!r} does not match {request.category!r}",
                    confidence=1.0,
                ).model_dump(mode="json")
            )
            continue
        remaining.append(candidate)
    return {
        "filtered_candidates": [candidate.model_dump(mode="json") for candidate in remaining],
        "pre_rejections": rejections,
    }


def _candidate_shape_error(
    candidate: CatalogCandidate,
    known_reference_keys: set[str] | None = None,
) -> str | None:
    required_fields = {
        "title": candidate.title,
        "learner_question": candidate.learner_question,
        "scope": candidate.scope,
        "rationale": candidate.rationale,
        "teachable_core": candidate.teachable_core,
        "independent_difference": candidate.independent_difference,
    }
    missing = sorted(name for name, value in required_fields.items() if not value.strip())
    if missing:
        return f"candidate is missing required teachable fields: {', '.join(missing)}"
    lowered_title = normalize_text(candidate.title)
    banned = sorted(term for term in K_CATALOG_TITLE_BANNED_TERMS if term in lowered_title.split())
    if banned:
        return "candidate title uses internal jargon: " + ", ".join(banned)
    if known_reference_keys is not None:
        unknown_neighbours = [
            value for value in candidate.nearest_existing if normalize_slug(value) not in known_reference_keys
        ]
        if unknown_neighbours:
            return "candidate has unknown nearest_existing references: " + ", ".join(sorted(unknown_neighbours))
        unknown_prerequisites = [
            value for value in candidate.prerequisites if normalize_slug(value) not in known_reference_keys
        ]
        if unknown_prerequisites:
            return "candidate has unknown prerequisite references: " + ", ".join(sorted(unknown_prerequisites))
    return None


def _known_reference_keys(
    existing_lessons: list[ExistingLesson],
    candidates: list[CatalogCandidate],
) -> set[str]:
    """Return canonical keys accepted in candidate references."""
    keys = {
        normalize_slug(value) for lesson in existing_lessons for value in (lesson.slug, lesson.title, *lesson.aliases)
    }
    keys.update(
        normalize_slug(value)
        for candidate in candidates
        for value in (candidate.slug, candidate.title, *candidate.alternate_labels)
    )
    return {key for key in keys if key}


def _categories_match(candidate_category: str, requested_category: str) -> bool:
    """Require candidates to use the requested lesson-owner category."""
    candidate_key = normalize_text(candidate_category)
    requested_key = normalize_text(requested_category)
    return candidate_key == requested_key


__all__ = ["existing_filter_node"]
