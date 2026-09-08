"""Entry points: ``discover_reviewer_node`` and ``build_discover_reviewer_prompt``.

The stage owns the independent offline discovery node and its catalog-review
prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.nodes.node_support import error_message
from lesson_builder.workflow.catalog_design.nodes.node_support import existing_lessons
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.nodes.node_support import set_source
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def discover_reviewer_node(state: CatalogState, deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Run the independent, no-browsing review branch."""
    try:
        batch = CandidateBatch.model_validate(
            deps.discover_reviewer(request_from_state(state), existing_lessons(state))
        )
    except Exception as exc:  # noqa: BLE001 - graph records branch failure for review
        return {"reviewer_candidates": [], "errors": [error_message("Catalog review discovery", exc)]}
    candidates = [set_source(candidate, "reviewer") for candidate in batch.candidates]
    return {"reviewer_candidates": [candidate.model_dump(mode="json") for candidate in candidates]}


def build_discover_reviewer_prompt(
    request: CatalogRequest,
    *,
    existing_snapshot: str,
    quality_contract: str,
) -> str:
    """Build the offline catalog-review discovery prompt."""
    return f"""
You are the offline discovery reviewer for a Norwegian language-course catalog.
Use the supplied context and model knowledge.

Category: {request.category}
Category guidance: {request.category_guidance or "No extra guidance."}
CEFR tags are optional metadata only: {", ".join(request.cefr_tags) or "none supplied"}

Existing catalog snapshot (semantic coverage map):
{existing_snapshot}

{quality_contract}

Return at most eight independently teachable owners. Keep each field concise:
state the learner question, bounded scope, exclusions, prerequisites, common
confusions, teachable core, nearest existing owner, independent difference, and
standalone rationale. Cover the most important gaps rather than
trying to enumerate the whole category. Include communicative functions where
the requested category calls for them, but keep sound and writing proposals in
their own category boundaries.
Exclude title-only ideas, vocabulary lists, examples masquerading as owners,
broad umbrellas containing unrelated learner decisions, and source fields.
Use canonical slugs from the existing snapshot for `nearest_existing` and
`prerequisites`; leave a list empty instead of inventing an identifier.

Completion criterion: return at most eight candidates, and for every returned
candidate fill every required contract field with a bounded, independently
teachable owner.
"""


__all__ = ["build_discover_reviewer_prompt", "discover_reviewer_node"]
