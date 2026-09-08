"""Entry points: ``discover_explorer_node`` and ``build_discover_explorer_prompt``.

The stage owns the explorer node and its web-research prompt. The dependency
builder supplies the request and deterministic catalog context.
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


def discover_explorer_node(state: CatalogState, deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Run the explorer's independent, internet-backed discovery branch."""
    try:
        batch = CandidateBatch.model_validate(
            deps.discover_explorer(request_from_state(state), existing_lessons(state))
        )
    except Exception as exc:  # noqa: BLE001 - graph records branch failure for review
        return {"explorer_candidates": [], "errors": [error_message("Explorer discovery", exc)]}
    candidates = [set_source(candidate, "explorer") for candidate in batch.candidates]
    return {"explorer_candidates": [candidate.model_dump(mode="json") for candidate in candidates]}


def build_discover_explorer_prompt(
    request: CatalogRequest,
    *,
    existing_snapshot: str,
    quality_contract: str,
) -> str:
    """Build the web-informed catalog discovery prompt."""
    return f"""
You are the explorer for a Norwegian language-course catalog. Research the
public web before proposing candidate owners for the requested category.

Category: {request.category}
Category guidance: {request.category_guidance or "No extra guidance."}
CEFR tags are optional metadata only: {", ".join(request.cefr_tags) or "none supplied"}

Existing catalog snapshot (semantic coverage map):
{existing_snapshot}

{quality_contract}

For every candidate, state one learner question, a bounded scope, what is out of
scope, likely prerequisites/confusions, a teachable core, the nearest existing
owner, the independent difference, and why it deserves a standalone lesson.
Use canonical slugs from the existing snapshot for `nearest_existing` and
`prerequisites`; leave a list empty instead of inventing an identifier.
Use research as private input; the response contains only the candidate contract,
with no source or evidence fields. Prefer communicative usefulness and teachable
boundaries over title-only items, vocabulary lists, synonyms, examples, and
umbrella owners containing independent learner decisions. Scope candidates in
the repository's own taxonomy.

Completion criterion: return at most eight candidates, and for every returned
candidate fill every required contract field with canonical existing slugs where
references apply.
    """


__all__ = ["build_discover_explorer_prompt", "discover_explorer_node"]
