"""Entry point: ``CatalogState`` and ``route_after_evaluation``.

The state intentionally contains proposal-sized JSON objects so LangGraph
checkpoints can resume a run without touching the canonical lesson files.
"""

from __future__ import annotations

import hashlib
import json
import operator
from typing import Annotated
from typing import Literal
from typing import TypedDict

K_ROUTE_ITERATE: Literal["iterate"] = "iterate"
K_ROUTE_ADVICE: Literal["request_advice"] = "request_advice"
K_ROUTE_FINALIZE: Literal["finalize"] = "finalize"
K_STAGNATION_ADVICE_THRESHOLD = 2


class CatalogState(TypedDict, total=False):
    """Checkpointable state for one category-design run."""

    request: dict[str, object]
    repo_root: str
    run_id: str
    iteration: int
    max_iterations: int

    existing_lessons: list[dict[str, object]]
    explorer_candidates: list[dict[str, object]]
    reviewer_candidates: list[dict[str, object]]
    candidates: list[dict[str, object]]
    filtered_candidates: list[dict[str, object]]
    pre_rejections: list[dict[str, object]]
    resolutions: list[dict[str, object]]
    evaluations: list[dict[str, object]]

    architecture_question: bool
    architecture_question_reason: str
    coverage_complete: bool
    coverage_gaps: list[str]
    advice: str | None
    advice_used: bool
    stagnation_count: int
    last_fingerprint: str | None

    errors: Annotated[list[str], operator.add]
    proposal: dict[str, object]


def route_after_evaluation(state: CatalogState) -> Literal["iterate", "request_advice", "finalize"]:
    """Route a round to one bounded retry, review advice, or finalization."""
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 1)
    architecture_question = state.get("architecture_question", False)
    stagnation_count = state.get("stagnation_count", 0)
    if not state.get("advice_used") and (architecture_question or stagnation_count >= K_STAGNATION_ADVICE_THRESHOLD):
        return K_ROUTE_ADVICE
    if _needs_more_work(state) and iteration + 1 < max_iterations:
        return K_ROUTE_ITERATE
    return K_ROUTE_FINALIZE


def resolution_fingerprint(state: CatalogState) -> str:
    """Hash the semantic result, not raw candidate count, for stagnation routing."""
    payload = {
        "resolutions": state.get("resolutions", []),
        "evaluations": state.get("evaluations", []),
        "pre_rejections": state.get("pre_rejections", []),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _needs_more_work(state: CatalogState) -> bool:
    if state.get("architecture_question"):
        return True
    if not state.get("resolutions"):
        return True
    if not state.get("coverage_complete", False):
        return True
    return any(evaluation.get("status") == "needs_human_review" for evaluation in state.get("evaluations", []))


__all__ = [
    "CatalogState",
    "K_ROUTE_ADVICE",
    "K_ROUTE_FINALIZE",
    "K_ROUTE_ITERATE",
    "K_STAGNATION_ADVICE_THRESHOLD",
    "resolution_fingerprint",
    "route_after_evaluation",
]
