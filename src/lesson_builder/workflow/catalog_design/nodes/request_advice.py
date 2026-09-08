"""Entry points: ``request_advice_node`` and ``build_request_advice_prompt``.

The stage owns the bounded architecture-advice node and its prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.workflow.catalog_design.models import AdviceResult
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import error_message
from lesson_builder.workflow.catalog_design.nodes.node_support import evaluation_list
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.nodes.node_support import resolution_list
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def request_advice_node(state: CatalogState, deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Ask catalog review once for a bounded architecture diagnosis."""
    reason = state.get("architecture_question_reason", "") or (
        f"the resolution fingerprint was unchanged for {state.get('stagnation_count', 0)} rounds"
    )
    try:
        result = AdviceResult.model_validate(
            deps.advise(
                request_from_state(state),
                candidate_list(state.get("filtered_candidates", [])),
                resolution_list(state.get("resolutions", [])),
                evaluation_list(state.get("evaluations", [])),
                reason,
            )
        )
    except Exception as exc:  # noqa: BLE001 - graph can finalize with review status
        return {"advice_used": True, "errors": [error_message("Catalog review advice", exc)]}
    return {
        "advice": result.advice,
        "advice_used": True,
        "architecture_question": result.architecture_question,
        "architecture_question_reason": reason,
    }


def build_request_advice_prompt(
    request: CatalogRequest,
    reason: str,
    *,
    candidates_payload: str,
    resolutions_payload: str,
    evaluations_payload: str,
) -> str:
    """Build the catalog-review architecture-advice prompt."""
    return f"""
You are the architecture advisor for a checkpointed catalog-design graph.
Diagnose why this category-filling run is stagnant or ambiguous. Supply one
concrete prompt, category-boundary, merge/split, or evaluation-policy adjustment
for the next round. Keep candidate dispositions unchanged and use the
repository's taxonomy.

Completion criterion: return one bounded diagnosis and one actionable adjustment.

Category: {request.category}
Trigger: {reason}
Candidates:
{candidates_payload}
Resolutions:
{resolutions_payload}
Evaluations:
{evaluations_payload}
"""


__all__ = ["build_request_advice_prompt", "request_advice_node"]
