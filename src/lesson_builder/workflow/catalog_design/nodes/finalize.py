"""Entry point: ``finalize_catalog_node`` (registered as ``finalize``).

``build_catalog_graph`` calls this node to build the human-readable proposal
from the accumulated decisions; it never writes canonical lesson files. The
deterministic status and quality-floor policy below cannot be overridden by a
model score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogDecision
from lesson_builder.workflow.catalog_design.models import CatalogProposal
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import DecisionStatus
from lesson_builder.workflow.catalog_design.models import LlmCallProvenance
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import evaluation_list
from lesson_builder.workflow.catalog_design.nodes.node_support import rejection
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.nodes.node_support import resolution_list
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_MIN_DIMENSION_SCORE
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_MIN_QUALITY_SCORE
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_MIN_RESOLUTION_CONFIDENCE
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_QUALITY_DIMENSIONS
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def finalize_catalog_node(state: CatalogState, _deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Build the human-readable proposal and never write canonical lesson files."""
    request = request_from_state(state)
    candidates = candidate_list(state.get("candidates", []))
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    evaluations = evaluation_list(state.get("evaluations", []))
    evaluation_by_slug = {normalize_slug(item.canonical_slug): item for item in evaluations}
    decisions = [CatalogDecision.model_validate(item) for item in state.get("pre_rejections", [])]
    assigned: set[str] = set()

    for resolution in resolution_list(state.get("resolutions", [])):
        group = [candidate_by_id[item] for item in resolution.candidate_ids if item in candidate_by_id]
        assigned.update(candidate.candidate_id for candidate in group)
        evaluation = evaluation_by_slug.get(normalize_slug(resolution.canonical_slug))
        decisions.append(_decision_for_resolution(request, resolution, group, evaluation))

    for candidate in candidates:
        if candidate.candidate_id not in assigned and not any(
            candidate.candidate_id in decision.candidate_ids for decision in decisions
        ):
            decisions.append(
                rejection(
                    candidate,
                    title=candidate.title,
                    status="rejected_invalid",
                    relationship=None,
                    reason="candidate was not assigned to a semantic resolution",
                    confidence=0.0,
                )
            )

    status = _proposal_status(
        decisions,
        state.get("errors", []),
        candidates,
        coverage_complete=state.get("coverage_complete", False),
        architecture_question=state.get("architecture_question", False),
    )
    proposal = CatalogProposal(
        category=request.category,
        cefr_tags=request.cefr_tags,
        candidates=candidates,
        resolutions=resolution_list(state.get("resolutions", [])),
        evaluations=evaluations,
        decisions=decisions,
        iteration=state.get("iteration", 0),
        stagnation_count=state.get("stagnation_count", 0),
        advice=state.get("advice"),
        coverage_complete=state.get("coverage_complete", False),
        coverage_gaps=list(state.get("coverage_gaps", [])),
        errors=list(state.get("errors", [])),
        llm_provenance=[LlmCallProvenance.model_validate(item) for item in _deps.provenance],
        status=status,
    )
    return {"proposal": proposal.model_dump(mode="json")}


def _decision_for_resolution(
    request: CatalogRequest,
    resolution: CandidateResolution,
    group: list[CatalogCandidate],
    evaluation: CandidateEvaluation | None,
) -> CatalogDecision:
    aliases = sorted({*resolution.aliases, *(label for candidate in group for label in candidate.alternate_labels)})
    confidence = resolution.confidence if evaluation is None else min(resolution.confidence, evaluation.quality_score)
    if resolution.relationship == "lesson_extension":
        status, reason, confidence = _extension_decision(resolution, group, evaluation, confidence)
    else:
        status, reason, confidence = _standard_decision(resolution, group, evaluation, confidence)
    return CatalogDecision(
        canonical_slug=resolution.canonical_slug,
        title=resolution.canonical_title,
        category=request.category,
        cefr_tags=sorted({tag for candidate in group for tag in candidate.cefr_tags}),
        candidate_ids=list(resolution.candidate_ids),
        aliases=aliases,
        relationship=resolution.relationship,
        target_lesson_slug=resolution.existing_slug if resolution.relationship == "lesson_extension" else None,
        teaching_point=resolution.teaching_point,
        status=status,
        reason=reason,
        confidence=max(0.0, min(1.0, confidence)),
    )


def _extension_decision(
    resolution: CandidateResolution,
    group: list[CatalogCandidate],
    evaluation: CandidateEvaluation | None,
    confidence: float,
) -> tuple[DecisionStatus, str, float]:
    """Resolve the deterministic status policy for a lesson extension."""
    if not resolution.existing_slug:
        return "rejected_invalid", "lesson_extension must name an existing lesson owner", 0.0
    if not group:
        return "rejected_invalid", "lesson_extension references no known candidate", 0.0
    if evaluation is None:
        return (
            "needs_human_review",
            "Catalog review did not return a quality evaluation for this teaching-point extension",
            confidence,
        )
    return _evaluated_decision(
        resolution,
        evaluation,
        accepted_status="accepted",
        fallback_reason="Catalog review did not return a quality evaluation for this teaching-point extension",
        confidence=confidence,
    )


def _standard_decision(
    resolution: CandidateResolution,
    group: list[CatalogCandidate],
    evaluation: CandidateEvaluation | None,
    confidence: float,
) -> tuple[DecisionStatus, str, float]:
    """Resolve the deterministic status policy for a normal resolution."""
    if resolution.existing_slug or resolution.relationship in {"duplicate", "exact_duplicate", "semantic_duplicate"}:
        return (
            "rejected_existing" if resolution.existing_slug else "rejected_duplicate",
            resolution.rationale,
            confidence,
        )
    if not group:
        return "rejected_invalid", "resolution references no known candidate", 0.0
    if resolution.relationship in {"uncertain", "broader", "narrower", "related"}:
        return "needs_human_review", resolution.review_question or resolution.rationale, confidence
    if evaluation is None:
        return (
            "needs_human_review",
            "Catalog review did not return a quality evaluation for this resolution",
            confidence,
        )
    return _evaluated_decision(
        resolution,
        evaluation,
        accepted_status="merged" if resolution.relationship in {"merge", "merge_candidate"} else "accepted",
        fallback_reason="Catalog review did not return a quality evaluation for this resolution",
        confidence=confidence,
    )


def _evaluated_decision(
    resolution: CandidateResolution,
    evaluation: CandidateEvaluation,
    *,
    accepted_status: DecisionStatus,
    fallback_reason: str,
    confidence: float,
) -> tuple[DecisionStatus, str, float]:
    """Apply shared evaluation floors to a resolution."""
    if evaluation.status == "needs_human_review" or resolution.confidence < K_CATALOG_MIN_RESOLUTION_CONFIDENCE:
        return "needs_human_review", "; ".join(evaluation.reasons) or resolution.rationale, confidence
    if _below_quality_floor(evaluation):
        return (
            "rejected_low_quality",
            "; ".join(evaluation.reasons) or "quality score is below the deterministic catalog floor",
            confidence,
        )
    if evaluation.status == "reject" or _below_soft_quality_floor(evaluation):
        return (
            "needs_human_review",
            "; ".join(evaluation.reasons) or "one quality dimension needs human review",
            confidence,
        )
    return accepted_status, resolution.rationale or fallback_reason, confidence


def _proposal_status(
    decisions: list[CatalogDecision],
    errors: list[str],
    candidates: list[CatalogCandidate],
    *,
    coverage_complete: bool,
    architecture_question: bool,
) -> Literal["ready", "partial", "needs_human_review", "blocked", "failed"]:
    approval_ready = any(decision.status in {"accepted", "merged"} for decision in decisions)
    human_review = architecture_question or any(decision.status == "needs_human_review" for decision in decisions)
    if not candidates and errors:
        return "blocked" if all(_is_operational_error(error) for error in errors) else "failed"
    if approval_ready:
        if errors or not coverage_complete or human_review:
            return "partial"
        return "ready"
    if errors and all(_is_operational_error(error) for error in errors):
        return "blocked"
    if human_review or not coverage_complete:
        return "needs_human_review"
    return "ready"


def _below_quality_floor(
    evaluation: CandidateEvaluation,
) -> bool:
    hard_failures = evaluation.hard_failures
    if hard_failures:
        return True
    return evaluation.quality_score < K_CATALOG_MIN_QUALITY_SCORE


def _below_soft_quality_floor(
    evaluation: CandidateEvaluation,
) -> bool:
    """Identify one weak soft dimension without auto-rejecting the candidate."""
    return any(getattr(evaluation, name) < K_CATALOG_MIN_DIMENSION_SCORE for name in K_CATALOG_QUALITY_DIMENSIONS)


def _is_operational_error(error: str) -> bool:
    lowered = error.casefold()
    return any(
        marker in lowered
        for marker in (
            "discovery failed",
            "quota",
            "backenddown",
            "database is locked",
            "adapter unavailable",
        )
    )


__all__ = ["finalize_catalog_node"]
