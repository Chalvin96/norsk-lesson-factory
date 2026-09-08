"""Entry points: shared catalog-node state and decision builders.

These public helpers are used by more than one node package. They stay in the
nodes package because they project graph state and build graph decisions; they
are not generic utilities.
"""

from __future__ import annotations

from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogDecision
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import DecisionStatus
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.models import Relationship
from lesson_builder.workflow.catalog_design.state import CatalogState


def candidate_list(payload: list[dict[str, object]]) -> list[CatalogCandidate]:
    """Validate candidate payloads from graph state."""
    return [CatalogCandidate.model_validate(item) for item in payload]


def resolution_list(payload: list[dict[str, object]]) -> list[CandidateResolution]:
    """Validate resolution payloads from graph state."""
    return [CandidateResolution.model_validate(item) for item in payload]


def evaluation_list(payload: list[dict[str, object]]) -> list[CandidateEvaluation]:
    """Validate evaluation payloads from graph state."""
    return [CandidateEvaluation.model_validate(item) for item in payload]


def existing_lessons(state: CatalogState) -> list[ExistingLesson]:
    """Validate existing-lesson records from graph state."""
    return [ExistingLesson.model_validate(item) for item in state.get("existing_lessons", [])]


def request_from_state(state: CatalogState) -> CatalogRequest:
    """Validate the catalog request from graph state."""
    return CatalogRequest.model_validate(state.get("request", {}))


def error_message(label: str, exc: Exception) -> str:
    """Format a bounded graph-visible node error."""
    return f"{label} failed: {type(exc).__name__}: {str(exc)[:500]}"


def set_source(candidate: CatalogCandidate, source: str) -> CatalogCandidate:
    """Attach the discovery branch that produced a candidate."""
    return candidate.model_copy(update={"source_agent": source})


def rejection(
    candidate: CatalogCandidate,
    *,
    title: str,
    status: DecisionStatus,
    relationship: Relationship | None,
    reason: str,
    confidence: float,
) -> CatalogDecision:
    """Build a catalog decision that rejects or defers one candidate."""
    return CatalogDecision(
        canonical_slug=candidate.slug,
        title=title,
        category=candidate.category,
        cefr_tags=candidate.cefr_tags,
        candidate_ids=[candidate.candidate_id],
        aliases=candidate.alternate_labels,
        relationship=relationship,
        status=status,
        reason=reason,
        confidence=confidence,
    )


__all__ = [
    "candidate_list",
    "error_message",
    "evaluation_list",
    "existing_lessons",
    "rejection",
    "request_from_state",
    "resolution_list",
    "set_source",
]
