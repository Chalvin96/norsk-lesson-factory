"""Entry points: ``evaluate_candidates_node`` and ``build_evaluate_candidates_prompt``.

The stage owns catalog-review quality evaluation and the prompt for that call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import error_message
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.nodes.node_support import resolution_list
from lesson_builder.workflow.catalog_design.state import CatalogState
from lesson_builder.workflow.catalog_design.state import resolution_fingerprint

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def evaluate_candidates_node(state: CatalogState, deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Ask catalog review for quality dimensions and update the fingerprint."""
    request = request_from_state(state)
    candidates = candidate_list(state.get("filtered_candidates", []))
    resolutions = resolution_list(state.get("resolutions", []))
    try:
        result = deps.evaluate(request, candidates, resolutions)
        evaluations = [CandidateEvaluation.model_validate(item) for item in result.evaluations]
        architecture_question = state.get("architecture_question", False) or result.architecture_question
    except Exception as exc:  # noqa: BLE001 - structured failure is part of proposal status
        return {
            "evaluations": [],
            "last_fingerprint": resolution_fingerprint(state),
            "coverage_complete": False,
            "coverage_gaps": [],
            "errors": [error_message("Catalog review evaluation", exc)],
        }
    fingerprint = resolution_fingerprint(
        {**state, "evaluations": [item.model_dump(mode="json") for item in evaluations]}
    )
    previous = state.get("last_fingerprint")
    stagnation_count = state.get("stagnation_count", 1) + 1 if previous == fingerprint else 1
    return {
        "evaluations": [evaluation.model_dump(mode="json") for evaluation in evaluations],
        "last_fingerprint": fingerprint,
        "stagnation_count": stagnation_count,
        "architecture_question": architecture_question,
        "coverage_complete": result.coverage_complete,
        "coverage_gaps": result.coverage_gaps,
    }


def build_evaluate_candidates_prompt(
    request: CatalogRequest,
    *,
    candidates_payload: str,
    resolutions_payload: str,
) -> str:
    """Build the catalog-review quality-evaluation prompt."""
    return f"""
You are the offline quality evaluator for a Norwegian catalog proposal. Score
each canonical resolution independently on [0,1] for distinctness, usefulness,
scope_clarity, category_fit, and accuracy. The quality_score should reflect the
weakest important dimension rather than hiding a serious defect in an average.

Also decide whether the category appears sufficiently covered by the proposed
set. Set coverage_complete=false and list concrete coverage_gaps when another
discovery round would add a meaningful learner decision. Do not manufacture
owners to hit a count.

Reject title-only, vague, over-broad, too-thin, inaccurate, out-of-category,
duplicate, or unsupported proposals. Treat missing stable learner decisions,
incomplete grammar contrasts, and multiple unrelated task boundaries as hard
failures even when other scores are high. Use needs_human_review only for one
narrow unresolved semantic or pragmatic boundary when the other dimensions pass.
Also check that each title is a short, active, learner-facing label in simple
controlled English. A title that depends on internal jargon or a dense noun
stack is not approval-ready; state the plain-language rewrite in the reason.
A score is advice; the graph enforces schema, exact-duplicate, and
quality-threshold rules deterministically. Return concise reason_codes and put
any non-compensable defects in hard_failures.

Completion criterion: return one evaluation for every canonical resolution,
plus one category-level coverage decision and every concrete coverage gap.

Category: {request.category}
Candidates:
{candidates_payload}
Resolutions:
{resolutions_payload}
"""


__all__ = ["build_evaluate_candidates_prompt", "evaluate_candidates_node"]
