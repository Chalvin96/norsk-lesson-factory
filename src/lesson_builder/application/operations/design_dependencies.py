"""Entry points: dependency design, prompt builders, and second-opinion retry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from typing import Protocol

import yaml

from lesson_builder.clients.llm.config import load_llm_job
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.jobs import catalog_reviewer
from lesson_builder.clients.llm.jobs import curriculum_reviewer
from lesson_builder.domain.catalog.models import DependencyAssessment
from lesson_builder.domain.catalog.models import DependencyEdge
from lesson_builder.domain.catalog.models import DependencyKind
from lesson_builder.domain.catalog.models import DependencyProposal
from lesson_builder.domain.catalog.models import DependencyProposalRequest
from lesson_builder.domain.catalog.models import DependencyReview
from lesson_builder.domain.catalog.models import DependencyReviewResult
from lesson_builder.domain.catalog.models import DependencySecondOpinion
from lesson_builder.domain.catalog.models import DependencySecondOpinionFinding
from lesson_builder.domain.catalog.models import DependencySecondOpinionRecord
from lesson_builder.domain.catalog.models import DependencySecondOpinionRequest
from lesson_builder.domain.catalog.models import OwnerResolution
from lesson_builder.domain.catalog.services.dependency_policy import build_cefr_by_owner as _build_cefr_by_owner
from lesson_builder.domain.catalog.services.dependency_policy import build_dependency_edges as _build_dependency_edges
from lesson_builder.domain.catalog.services.dependency_policy import build_owner_payload as _owner_payload
from lesson_builder.domain.catalog.services.dependency_policy import find_assessment as _assessment_for
from lesson_builder.domain.catalog.services.dependency_policy import normalize_assessments as _normalize_assessments
from lesson_builder.domain.catalog.services.dependency_policy import normalize_edges as _normalize_edges
from lesson_builder.domain.catalog.services.resolve_owner_references import build_owner_resolution
from lesson_builder.domain.catalog.services.validate_dependency_graph import validate_dependency_graph
from lesson_builder.domain.catalog.settings import K_CATALOG_DEPENDENCY_REVIEW_SCHEMA_VERSION
from lesson_builder.domain.catalog.settings import K_CATALOG_DEPENDENCY_SECOND_REVIEW_EDGE_BATCH_SIZE
from lesson_builder.domain.catalog.settings import K_CATALOG_DEPENDENCY_SECOND_REVIEW_MAX_WORKERS
from lesson_builder.domain.catalog.settings import K_CATALOG_DEPENDENCY_SECOND_REVIEW_RETRIES
from lesson_builder.domain.catalog.settings import K_CATALOG_DEPENDENCY_SECOND_REVIEW_TEXT_LIMIT
from lesson_builder.workspace.paths import WorkspacePaths

K_CATALOG_DEPENDENCY_MIN_SPLIT_EDGES = 2


class DependencyProposer(Protocol):
    """External proposal boundary, injected by tests and the live runner."""

    def __call__(self, request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal: ...


class DependencySecondOpinionReviewer(Protocol):
    """Independent review boundary, injected by tests and the live runner."""

    def __call__(
        self,
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion: ...


def run_dependency_design(
    *,
    repo_root: Path,
    run_id: str,
    catalog_path: Path | None = None,
    proposal_service: DependencyProposer | None = None,
    second_opinion_service: DependencySecondOpinionReviewer | None = None,
) -> DependencyReviewResult:
    """Create a scratch owner-level dependency proposal without promotion."""
    root = Path(repo_root)
    paths = WorkspacePaths(root)
    source_path = (
        paths.catalog_file
        if catalog_path is None
        else (catalog_path if catalog_path.is_absolute() else root / catalog_path)
    )
    catalog = _load_mapping(source_path)
    _require_complete_catalog(catalog)

    resolution = build_owner_resolution(catalog)
    request = DependencyProposalRequest(owners=_owner_payload(catalog))
    provenance: list[dict[str, object]] = [_model_provenance("proposal", "catalog_reviewer", root)]
    service = proposal_service or _default_dependency_proposal
    try:
        proposal = service(request, repo_root=root)
    except Exception as exc:  # noqa: BLE001 - preserve live failure in scratch output
        review = _failed_review(
            run_id=run_id,
            repo_root=root,
            source_path=source_path,
            catalog=catalog,
            errors=[f"dependency proposal failed: {type(exc).__name__}: {exc}"],
            provenance=provenance,
        )
    else:
        review = _build_dependency_review(
            run_id=run_id,
            root=root,
            source_path=source_path,
            catalog=catalog,
            resolution=resolution,
            proposal=proposal,
            proposal_service=proposal_service,
            second_opinion_service=second_opinion_service,
            provenance=provenance,
        )
    return _write_review(root, run_id, review)


def rerun_dependency_second_opinion(
    *,
    repo_root: Path,
    source_review_path: Path,
    run_id: str,
    second_opinion_service: DependencySecondOpinionReviewer | None = None,
) -> DependencyReviewResult:
    """Retry only the independent review for one unchanged scratch proposal."""
    root = Path(repo_root)
    review_path = source_review_path if source_review_path.is_absolute() else root / source_review_path
    review = DependencyReview.model_validate(_load_mapping(review_path))
    source_path = root / review.source_catalog
    catalog = _load_mapping(source_path)
    current_hash = _content_hash(catalog)
    if current_hash != review.source_catalog_hash:
        raise ValueError(
            "cannot retry second opinion against a changed catalog: "
            f"review={review.source_catalog_hash}, current={current_hash}"
        )
    resolution = build_owner_resolution(catalog)
    request = DependencySecondOpinionRequest(
        owners=_owner_payload(catalog),
        assessments=review.assessments,
        proposed_edges=review.proposed_edges,
        validation_errors=review.validation_errors,
    )
    reviewer = second_opinion_service or _default_second_opinion
    second_opinion_errors: list[str] = []
    try:
        result = reviewer(request, repo_root=root)
        second_opinion, second_opinion_errors = _normalize_second_opinion(
            result,
            resolution,
            review.proposed_edges,
            root,
        )
        provenance_entry = _model_provenance("second_opinion", "curriculum_reviewer", root)
    except Exception as exc:  # noqa: BLE001 - preserve a failed retry in scratch output
        second_opinion = _failed_second_opinion(root, exc)
        second_opinion_errors.append(f"second-opinion review failed: {type(exc).__name__}: {exc}")
        provenance_entry = _model_provenance("second_opinion", "curriculum_reviewer", root, error=str(exc))
    non_second_opinion_unresolved = [item for item in review.unresolved if not item.startswith("second-opinion")]
    unresolved = sorted({*non_second_opinion_unresolved, *second_opinion_errors})
    updated = review.model_copy(
        update={
            "run_id": run_id,
            "status": "ready_for_promotion"
            if not unresolved and not review.validation_errors
            else "needs_human_review",
            "second_opinion": second_opinion,
            "unresolved": unresolved,
            "provenance": [entry for entry in review.provenance if entry.get("role") != "second_opinion"]
            + [provenance_entry],
        }
    )
    return _write_review(root, run_id, updated)


def build_dependency_prompt(request: DependencyProposalRequest) -> str:
    """Render a compact, direction-explicit dependency-design prompt."""
    owners = json.dumps(request.owners, ensure_ascii=False, sort_keys=True)
    return f"""
You are the offline curriculum dependency designer for a Norwegian course.
Design prerequisite edges between approved lesson owners.

Use only active owner IDs from the owner index. Return exactly one assessment
for every owner, including an explicit empty assessment when no prerequisite is
needed. A required edge means the dependent assessment needs prior mastery of
the prerequisite; account for phrases or scaffolding supplied by the task. A helpful edge is useful context but must not constrain order.
Use active IDs verbatim, keep one lesson per owner, and base edges on assessment
dependencies rather than shared family membership.

Owner index:
{owners}

Explain uncertain or conflicting decisions in the assessment rationale and use
status=needs_human_review when the supplied outcomes leave an edge undecidable.
Completion criterion: return exactly one assessment for every owner, including
an explicit empty assessment when no prerequisite is needed.
"""


def build_second_opinion_prompt(request: DependencySecondOpinionRequest) -> str:
    """Render a compact read-only review prompt for the second model."""
    owners = json.dumps(
        _second_opinion_owner_view(request.owners),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assessments = json.dumps(
        [
            {
                "owner_id": assessment.owner_id,
                "required_prerequisites": assessment.required_prerequisites,
                "helpful_prerequisites": assessment.helpful_prerequisites,
                "status": assessment.status,
            }
            for assessment in request.assessments
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    edges = json.dumps(
        [
            {
                "prerequisite_id": edge.prerequisite_id,
                "dependent_id": edge.dependent_id,
                "kind": edge.kind,
                "rationale": _compact_text(edge.rationale),
            }
            for edge in request.proposed_edges
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    validation_errors = json.dumps(request.validation_errors, ensure_ascii=False, separators=(",", ":"))
    return f"""
You are the independent second reviewer for a Norwegian curriculum dependency
proposal. Review the supplied edge batch against the owner outcomes and return
advisory findings.

This is a bounded edge batch. The owner index below is complete for every owner
ID that appears in this batch; treat every listed ID as active. Review the edges
shown here.

A required edge means the learner cannot pass the dependent lesson's approved
assessment without prior mastery of the prerequisite. A helpful edge is useful
context but must not constrain course order. Be conservative: an explicit empty
prerequisite list is valid. Do not infer edges merely from shared family labels.

Use only exact active owner IDs from the owner index. Findings may keep, reject,
upgrade, downgrade, or add one edge. Use needs_human_review for an uncertain
decision. Every finding must include both endpoint IDs, severity, recommendation,
and a concise rationale. If an edge of the requested kind is already present in
Primary proposed edges, use keep rather than add_*; do not label a no-op P0/P1.
Keep findings scoped to individual edge decisions.

Owner index:
{owners}

Primary assessments:
{assessments}

Primary proposed edges:
{edges}

Deterministic validation errors:
{validation_errors}

Completion criterion: address every proposed edge once and add findings only for
concrete keep/reject/upgrade/downgrade/add decisions or one bounded uncertainty.
"""


def _require_complete_catalog(catalog: Mapping[str, Any]) -> None:
    """Require the approved catalog status before asking a model for edges."""
    if str(catalog.get("catalog_status", "")).strip() != "complete":
        raise ValueError("approved catalog must have catalog_status=complete before dependency design")


def _build_dependency_review(
    *,
    run_id: str,
    root: Path,
    source_path: Path,
    catalog: Mapping[str, Any],
    resolution: OwnerResolution,
    proposal: DependencyProposal,
    proposal_service: DependencyProposer | None,
    second_opinion_service: DependencySecondOpinionReviewer | None,
    provenance: list[dict[str, object]],
) -> DependencyReview:
    """Normalize a proposal, collect deterministic errors, and run review."""
    assessments, assessment_errors = _normalize_assessments(proposal.assessments, resolution)
    proposed_edges, edge_errors = _normalize_edges(proposal.edges, resolution)
    proposed_edges = proposed_edges or _build_dependency_edges(assessments)
    owner_ids = set(resolution.active_ids)
    cefr_by_owner = _build_cefr_by_owner(catalog)
    validation_errors = sorted(set(validate_dependency_graph(owner_ids, proposed_edges, cefr_by_owner=cefr_by_owner)))
    unresolved = _collect_proposal_unresolved_items(
        assessments, assessment_errors, edge_errors, owner_ids, cefr_by_owner
    )
    second_opinion, second_errors = _run_second_opinion(
        catalog=catalog,
        assessments=assessments,
        proposed_edges=proposed_edges,
        validation_errors=validation_errors,
        resolution=resolution,
        root=root,
        proposal_service=proposal_service,
        second_opinion_service=second_opinion_service,
        provenance=provenance,
    )
    unresolved.extend(second_errors)
    return DependencyReview(
        schema_version=K_CATALOG_DEPENDENCY_REVIEW_SCHEMA_VERSION,
        run_id=run_id,
        source_catalog=str(source_path.relative_to(root)),
        source_catalog_hash=_content_hash(catalog),
        source_snapshot=str(catalog.get("snapshot", "")),
        catalog_schema_version=_schema_version(catalog),
        status="ready_for_promotion" if not unresolved and not validation_errors else "needs_human_review",
        owner_count=len(owner_ids),
        reviewed_owner_count=sum(assessment.status == "reviewed" for assessment in assessments),
        assessments=assessments,
        proposed_edges=proposed_edges,
        second_opinion=second_opinion,
        unresolved=sorted(set(unresolved)),
        validation_errors=validation_errors,
        provenance=provenance,
    )


def _collect_proposal_unresolved_items(
    assessments: Sequence[DependencyAssessment],
    assessment_errors: Sequence[str],
    edge_errors: Sequence[str],
    owner_ids: set[str],
    cefr_by_owner: Mapping[str, str],
) -> list[str]:
    """Collect deterministic proposal gaps in their established order."""
    unresolved = [*assessment_errors, *edge_errors]
    unresolved.extend(
        f"assessment requires human review: {assessment.owner_id}"
        for assessment in assessments
        if assessment.status == "needs_human_review"
    )
    unresolved.extend(
        f"missing assessment: {owner_id}"
        for owner_id in sorted(owner_ids)
        if not _assessment_for(assessments, owner_id)
    )
    unresolved.extend(f"missing CEFR: {owner_id}" for owner_id, level in sorted(cefr_by_owner.items()) if not level)
    return unresolved


def _run_second_opinion(
    *,
    catalog: Mapping[str, Any],
    assessments: Sequence[DependencyAssessment],
    proposed_edges: Sequence[DependencyEdge],
    validation_errors: Sequence[str],
    resolution: OwnerResolution,
    root: Path,
    proposal_service: DependencyProposer | None,
    second_opinion_service: DependencySecondOpinionReviewer | None,
    provenance: list[dict[str, object]],
) -> tuple[DependencySecondOpinionRecord | None, list[str]]:
    """Run the second opinion when the original operation requests it."""
    if second_opinion_service is None and proposal_service is not None:
        return None, []
    reviewer = second_opinion_service or _default_second_opinion
    request = DependencySecondOpinionRequest(
        owners=_owner_payload(catalog),
        assessments=list(assessments),
        proposed_edges=list(proposed_edges),
        validation_errors=list(validation_errors),
    )
    try:
        result = reviewer(request, repo_root=root)
        opinion, errors = _normalize_second_opinion(result, resolution, proposed_edges, root)
        provenance.append(_model_provenance("second_opinion", "curriculum_reviewer", root))
        return opinion, errors
    except Exception as exc:  # noqa: BLE001 - preserve live failure in scratch output
        provenance.append(_model_provenance("second_opinion", "curriculum_reviewer", root, error=str(exc)))
        return _failed_second_opinion(root, exc), [f"second-opinion review failed: {type(exc).__name__}: {exc}"]


def _default_dependency_proposal(
    request: DependencyProposalRequest,
    *,
    repo_root: Path,
) -> DependencyProposal:
    """Ask the configured no-browsing reviewer job for owner-level edges."""
    agent = catalog_reviewer(repo_root=repo_root)
    prompt = build_dependency_prompt(request)
    return agent.structured(DependencyProposal).invoke(prompt)


def _default_second_opinion(
    request: DependencySecondOpinionRequest,
    *,
    repo_root: Path,
) -> DependencySecondOpinion:
    """Ask the configured independent reviewer in bounded parallel batches."""
    batches = _build_second_opinion_batches(request)

    with ThreadPoolExecutor(max_workers=min(K_CATALOG_DEPENDENCY_SECOND_REVIEW_MAX_WORKERS, len(batches))) as pool:
        opinions = list(pool.map(lambda batch: _review_second_opinion_batch(batch, repo_root), batches))
    return _combine_second_opinions(opinions)


def _review_second_opinion_batch(
    batch: DependencySecondOpinionRequest,
    repo_root: Path,
) -> DependencySecondOpinion:
    """Review one batch, splitting it only after backend retries are exhausted."""
    result = _invoke_second_opinion_batch(batch, repo_root)
    if result is not None:
        return result
    split_batches = _split_second_opinion_request(batch)
    if len(split_batches) == 1:
        raise BackendDownException("second-opinion batch exhausted retries")
    return _combine_second_opinions(
        [_review_second_opinion_batch(split_batch, repo_root) for split_batch in split_batches]
    )


def _invoke_second_opinion_batch(
    batch: DependencySecondOpinionRequest,
    repo_root: Path,
) -> DependencySecondOpinion | None:
    """Try one second-opinion batch through the configured backend."""
    prompt = build_second_opinion_prompt(batch)
    for attempt in range(K_CATALOG_DEPENDENCY_SECOND_REVIEW_RETRIES + 1):
        try:
            agent = curriculum_reviewer(repo_root=repo_root)
            return agent.structured(DependencySecondOpinion).invoke(prompt)
        except BackendDownException:
            if attempt >= K_CATALOG_DEPENDENCY_SECOND_REVIEW_RETRIES:
                return None
    return None


def _combine_second_opinions(opinions: Sequence[DependencySecondOpinion]) -> DependencySecondOpinion:
    """Combine batch summaries and deduplicated findings in stable order."""
    summaries = [opinion.summary.strip() for opinion in opinions if opinion.summary.strip()]
    findings = _dedupe_second_opinion_findings(finding for opinion in opinions for finding in opinion.findings)
    return DependencySecondOpinion(summary=" ".join(summaries), findings=findings)


def _split_second_opinion_request(
    request: DependencySecondOpinionRequest,
) -> list[DependencySecondOpinionRequest]:
    """Split a failed review batch without expanding its owner context."""
    edge_keys = list(dict.fromkeys(_edge_key(edge) for edge in request.proposed_edges))
    if len(edge_keys) < K_CATALOG_DEPENDENCY_MIN_SPLIT_EDGES:
        return [request]
    midpoint = len(edge_keys) // 2
    subsets = (set(edge_keys[:midpoint]), set(edge_keys[midpoint:]))
    return [
        request.model_copy(
            update={
                "proposed_edges": [edge for edge in request.proposed_edges if _edge_key(edge) in selected],
            }
        )
        for selected in subsets
    ]


def _build_second_opinion_batches(
    request: DependencySecondOpinionRequest,
) -> list[DependencySecondOpinionRequest]:
    """Partition graph edges into compact batches while retaining endpoint context."""
    all_edges = _deduplicate_edges(request.proposed_edges)
    if not all_edges:
        return [request]

    owner_by_id = {
        owner_id: owner for owner in request.owners if isinstance((owner_id := owner.get("id")), str) and owner_id
    }
    assessment_by_id = {assessment.owner_id: assessment for assessment in request.assessments}
    return [
        _build_second_opinion_batch(request, selected_edges, owner_by_id, assessment_by_id)
        for selected_edges in _build_edge_batches(all_edges)
    ]


def _deduplicate_edges(edges: Sequence[DependencyEdge]) -> list[DependencyEdge]:
    """Keep the first occurrence of each directed edge and kind."""
    by_key: dict[tuple[str, str, str], DependencyEdge] = {}
    for edge in edges:
        by_key.setdefault(_edge_key(edge), edge)
    return list(by_key.values())


def _build_edge_batches(edges: Sequence[DependencyEdge]) -> list[list[DependencyEdge]]:
    """Partition unique edges into bounded review batches."""
    return [
        list(edges[start : start + K_CATALOG_DEPENDENCY_SECOND_REVIEW_EDGE_BATCH_SIZE])
        for start in range(0, len(edges), K_CATALOG_DEPENDENCY_SECOND_REVIEW_EDGE_BATCH_SIZE)
    ]


def _build_second_opinion_batch(
    request: DependencySecondOpinionRequest,
    selected_edges: Sequence[DependencyEdge],
    owner_by_id: Mapping[str, Mapping[str, object]],
    assessment_by_id: Mapping[str, Any],
) -> DependencySecondOpinionRequest:
    """Build one bounded request with endpoint and prerequisite context."""
    context_ids = _collect_batch_context_ids(selected_edges, assessment_by_id)
    selected_keys = {_edge_key(edge) for edge in selected_edges}
    return request.model_copy(
        update={
            "owners": [owner_by_id[owner_id] for owner_id in sorted(context_ids) if owner_id in owner_by_id],
            "assessments": [
                assessment_by_id[owner_id] for owner_id in sorted(context_ids) if owner_id in assessment_by_id
            ],
            "proposed_edges": [edge for edge in request.proposed_edges if _edge_key(edge) in selected_keys],
        }
    )


def _collect_batch_context_ids(
    selected_edges: Sequence[DependencyEdge],
    assessment_by_id: Mapping[str, Any],
) -> set[str]:
    """Return endpoints plus their declared prerequisite context."""
    context_ids = {endpoint for edge in selected_edges for endpoint in (edge.prerequisite_id, edge.dependent_id)}
    for owner_id in tuple(context_ids):
        assessment = assessment_by_id.get(owner_id)
        if assessment is not None:
            context_ids.update(assessment.required_prerequisites)
            context_ids.update(assessment.helpful_prerequisites)
    return context_ids


def _dedupe_second_opinion_findings(
    findings: Iterable[DependencySecondOpinionFinding],
) -> list[DependencySecondOpinionFinding]:
    """Keep one deterministic copy when a cross-batch edge is reviewed twice."""
    result: list[DependencySecondOpinionFinding] = []
    seen: set[tuple[str, str, str, str, DependencyKind | None]] = set()
    for finding in findings:
        identity = (
            finding.prerequisite_id,
            finding.dependent_id,
            finding.recommendation,
            finding.rationale,
            finding.current_kind,
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(finding)
    return result


def _second_opinion_owner_view(owners: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Keep outcome context while avoiding a context-window-sized duplicate payload."""
    result: list[dict[str, object]] = []
    for owner in owners:
        result.append(
            {
                "id": owner.get("id"),
                "catalog_kind": owner.get("catalog_kind"),
                "family_id": owner.get("family_id"),
                "title": _compact_text(str(owner.get("title", ""))),
                "learner_outcome": _compact_text(str(owner.get("learner_outcome", ""))),
                "cefr_tags": owner.get("cefr_tags", []),
            }
        )
    return result


def _compact_text(value: str) -> str:
    """Bound repeated model prose while preserving the start of each rationale."""
    text = " ".join(value.split())
    if len(text) <= K_CATALOG_DEPENDENCY_SECOND_REVIEW_TEXT_LIMIT:
        return text
    return text[: K_CATALOG_DEPENDENCY_SECOND_REVIEW_TEXT_LIMIT - 1].rstrip() + "…"


def _edge_key(edge: DependencyEdge) -> tuple[str, str, str]:
    """Return the stable identity for one directed dependency edge."""
    return edge.prerequisite_id, edge.dependent_id, edge.kind


def _normalize_second_opinion(
    opinion: DependencySecondOpinion,
    resolution: OwnerResolution,
    proposed_edges: Sequence[DependencyEdge],
    repo_root: Path,
) -> tuple[DependencySecondOpinionRecord, list[str]]:
    """Normalize second-opinion endpoints and preserve review-required findings."""
    findings: list[DependencySecondOpinionFinding] = []
    errors: list[str] = []
    for finding in opinion.findings:
        normalized_finding, finding_errors = _normalize_second_opinion_finding(
            finding,
            resolution,
            proposed_edges,
        )
        errors.extend(finding_errors)
        findings.append(normalized_finding)
    record = DependencySecondOpinionRecord(
        status="completed",
        job="curriculum_reviewer",
        model=_configured_job_model(repo_root, "curriculum_reviewer"),
        summary=opinion.summary,
        findings=findings,
    )
    return record, errors


def _normalize_second_opinion_finding(
    finding: DependencySecondOpinionFinding,
    resolution: OwnerResolution,
    proposed_edges: Sequence[DependencyEdge],
) -> tuple[DependencySecondOpinionFinding, list[str]]:
    """Normalize one finding and collect all errors in the original order."""
    normalized, errors, endpoints = _resolve_finding_endpoints(finding, resolution)
    if endpoints is not None:
        normalized, graph_errors = _apply_finding_graph_rules(normalized, endpoints, proposed_edges)
        errors.extend(graph_errors)
    errors.extend(_collect_finding_review_errors(normalized))
    return normalized, errors


def _resolve_finding_endpoints(
    finding: DependencySecondOpinionFinding,
    resolution: OwnerResolution,
) -> tuple[DependencySecondOpinionFinding, list[str], tuple[str, str] | None]:
    """Resolve finding endpoints while retaining unresolved input values."""
    prerequisite_id = resolution.resolve(finding.prerequisite_id)
    dependent_id = resolution.resolve(finding.dependent_id)
    updates: dict[str, str] = {}
    errors: list[str] = []
    if prerequisite_id is None:
        errors.append(f"second-opinion unresolved prerequisite endpoint: {finding.prerequisite_id}")
    else:
        updates["prerequisite_id"] = prerequisite_id
    if dependent_id is None:
        errors.append(f"second-opinion unresolved dependent endpoint: {finding.dependent_id}")
    else:
        updates["dependent_id"] = dependent_id
    endpoints = (prerequisite_id, dependent_id) if prerequisite_id is not None and dependent_id is not None else None
    return finding.model_copy(update=updates), errors, endpoints


def _apply_finding_graph_rules(
    finding: DependencySecondOpinionFinding,
    endpoints: tuple[str, str],
    proposed_edges: Sequence[DependencyEdge],
) -> tuple[DependencySecondOpinionFinding, list[str]]:
    """Apply graph-aware kind normalization and contradiction checks."""
    prerequisite_id, dependent_id = endpoints
    actual_kinds = _collect_edge_kinds(proposed_edges, prerequisite_id, dependent_id)
    proposed_kinds = _collect_edge_kinds(proposed_edges, prerequisite_id, dependent_id)
    normalized = finding
    if normalized.current_kind is None and len(actual_kinds) == 1:
        normalized = normalized.model_copy(update={"current_kind": next(iter(actual_kinds))})
    if normalized.severity in {"P0", "P1"} and _is_redundant_finding(normalized, proposed_kinds):
        normalized = normalized.model_copy(update={"severity": "P2"})
    return normalized, _finding_graph_errors(normalized, actual_kinds, proposed_kinds)


def _collect_edge_kinds(
    edges: Sequence[DependencyEdge],
    prerequisite_id: str,
    dependent_id: str,
) -> set[DependencyKind]:
    """Return kinds proposed for one directed endpoint pair."""
    return {
        edge.kind for edge in edges if edge.prerequisite_id == prerequisite_id and edge.dependent_id == dependent_id
    }


def _collect_finding_review_errors(finding: DependencySecondOpinionFinding) -> list[str]:
    """Return errors that require a human decision for one normalized finding."""
    errors: list[str] = []
    if finding.severity in {"P0", "P1"}:
        errors.append(
            "second-opinion severity requires human review: "
            f"{finding.severity} {finding.prerequisite_id} -> {finding.dependent_id}"
        )
    if finding.recommendation == "needs_human_review" and finding.severity not in {"P0", "P1"}:
        errors.append(f"second-opinion requires human review: {finding.prerequisite_id} -> {finding.dependent_id}")
    return errors


def _finding_graph_errors(
    finding: DependencySecondOpinionFinding,
    actual_kinds: set[DependencyKind],
    proposed_kinds: set[DependencyKind],
) -> list[str]:
    """Reject second-opinion recommendations that contradict the graph."""
    edge_label = f"{finding.prerequisite_id} -> {finding.dependent_id}"
    errors = _collect_current_kind_errors(finding, actual_kinds, edge_label)
    errors.extend(_collect_recommendation_graph_errors(finding, actual_kinds, proposed_kinds, edge_label))
    return errors


def _collect_current_kind_errors(
    finding: DependencySecondOpinionFinding,
    actual_kinds: set[DependencyKind],
    edge_label: str,
) -> list[str]:
    """Report a finding whose declared kind differs from the graph."""
    if finding.current_kind is None or finding.current_kind in actual_kinds:
        return []
    return [
        "second-opinion current_kind mismatch: "
        f"{edge_label} says {finding.current_kind}, graph has {sorted(actual_kinds) or ['absent']}"
    ]


def _collect_recommendation_graph_errors(
    finding: DependencySecondOpinionFinding,
    actual_kinds: set[DependencyKind],
    proposed_kinds: set[DependencyKind],
    edge_label: str,
) -> list[str]:
    """Report recommendation endpoints that contradict the proposed graph."""
    recommendation = finding.recommendation
    if recommendation == "keep":
        return _collect_keep_graph_errors(proposed_kinds, edge_label)
    if recommendation in {"reject", "downgrade_to_helpful", "upgrade_to_required"}:
        return _collect_reclassification_graph_errors(recommendation, actual_kinds, edge_label)
    if recommendation in {"add_required", "add_helpful"}:
        return _collect_addition_graph_errors(finding, recommendation, actual_kinds, proposed_kinds, edge_label)
    return []


def _collect_keep_graph_errors(proposed_kinds: set[DependencyKind], edge_label: str) -> list[str]:
    """Reject a keep recommendation for an absent edge."""
    if proposed_kinds:
        return []
    return [f"second-opinion keep references absent edge: {edge_label}"]


def _collect_reclassification_graph_errors(
    recommendation: str,
    actual_kinds: set[DependencyKind],
    edge_label: str,
) -> list[str]:
    """Reject a remove or kind-change recommendation for an invalid edge."""
    if not actual_kinds:
        return [f"second-opinion {recommendation} references absent edge: {edge_label}"]
    if recommendation == "downgrade_to_helpful" and "required" not in actual_kinds:
        return [f"second-opinion downgrade references non-required edge: {edge_label}"]
    if recommendation == "upgrade_to_required" and "helpful" not in actual_kinds:
        return [f"second-opinion upgrade references non-helpful edge: {edge_label}"]
    return []


def _collect_addition_graph_errors(
    finding: DependencySecondOpinionFinding,
    recommendation: str,
    actual_kinds: set[DependencyKind],
    proposed_kinds: set[DependencyKind],
    edge_label: str,
) -> list[str]:
    """Reject an add recommendation that duplicates or misstates an edge."""
    target_kind: DependencyKind = "required" if recommendation == "add_required" else "helpful"
    errors: list[str] = []
    if proposed_kinds and target_kind not in proposed_kinds:
        errors.append(f"second-opinion {recommendation} references existing edge: {edge_label}")
    if finding.current_kind is not None and finding.current_kind not in actual_kinds:
        errors.append(f"second-opinion {recommendation} has current_kind for absent edge: {edge_label}")
    return errors


def _is_redundant_finding(
    finding: DependencySecondOpinionFinding,
    proposed_kinds: set[DependencyKind],
) -> bool:
    """Identify a serious model recommendation that is already in the graph."""
    recommendation = finding.recommendation
    if recommendation == "add_required":
        return "required" in proposed_kinds
    if recommendation == "add_helpful":
        return "helpful" in proposed_kinds
    if recommendation == "downgrade_to_helpful":
        return "helpful" in proposed_kinds
    if recommendation == "upgrade_to_required":
        return "required" in proposed_kinds
    return False


def _failed_second_opinion(repo_root: Path, error: Exception) -> DependencySecondOpinionRecord:
    """Build a visible failed second-opinion record without a fallback model."""
    return DependencySecondOpinionRecord(
        status="failed",
        job="curriculum_reviewer",
        model=_configured_job_model(repo_root, "curriculum_reviewer"),
        error=f"{type(error).__name__}: {error}",
    )


def _configured_job_model(repo_root: Path, job_name: str) -> str:
    """Return configured model metadata without hiding injected-test services."""
    try:
        return load_llm_job(job_name, repo_root=repo_root).tier.model
    except (FileNotFoundError, ValueError):
        return ""


def _model_provenance(
    role: str,
    job_name: str,
    repo_root: Path,
    *,
    error: str | None = None,
) -> dict[str, object]:
    """Record the configured model boundary for one dependency call."""
    entry: dict[str, object] = {
        "role": role,
        "job": job_name,
        "model": _configured_job_model(repo_root, job_name),
        "status": "failed" if error else "completed",
    }
    if error:
        entry["error"] = error
    return entry


def _write_review(root: Path, run_id: str, review: DependencyReview) -> DependencyReviewResult:
    """Write one append-only scratch dependency review."""
    output_dir = WorkspacePaths(root).dependency_review_scratch_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    review_path = output_dir / "review.yaml"
    review_path.write_text(
        yaml.safe_dump(review.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(_render_review_readme(review), encoding="utf-8")
    return DependencyReviewResult(
        run_id=run_id,
        review_path=str(review_path.relative_to(root)),
        review=review,
    )


def _render_review_readme(review: DependencyReview) -> str:
    """Render an exception-first operator summary beside the YAML artifact."""
    lines = [
        "# Scratch curriculum dependency review",
        "",
        "This artifact is a model-assisted proposal. It is not a canonical catalog, "
        "does not change curriculum order, and requires human review before promotion.",
        "",
        f"- Status: `{review.status}`",
        f"- Owners: {review.owner_count}",
        f"- Model-assessed owners: {review.reviewed_owner_count}/{review.owner_count}",
        f"- Proposed edges: {len(review.proposed_edges)}",
        f"- Second opinion: {review.second_opinion.status if review.second_opinion else 'not run'}",
        "",
        "## Needs attention",
        "",
    ]
    lines.extend(_render_review_issue_lines(review))
    lines.extend(_render_review_provenance_lines(review))
    lines.extend(_render_second_opinion_lines(review))
    if not review.validation_errors and not review.unresolved:
        lines.extend(["No deterministic exceptions remain.", ""])
    lines.extend(_render_review_file_lines())
    return "\n".join(lines)


def _render_review_issue_lines(review: DependencyReview) -> list[str]:
    """Render deterministic and unresolved items for the operator summary."""
    lines: list[str] = []
    if review.validation_errors:
        lines.append("### Validation errors")
        lines.extend(f"- {error}" for error in review.validation_errors)
        lines.append("")
    if review.unresolved:
        lines.append("### Unresolved items")
        lines.extend(f"- {item}" for item in review.unresolved)
        lines.append("")
    return lines


def _render_review_provenance_lines(review: DependencyReview) -> list[str]:
    """Render model provenance entries when they are available."""
    if not review.provenance:
        return []
    lines = ["### Model provenance", ""]
    lines.extend(
        "- "
        f"{entry.get('role', 'unknown')}: `{entry.get('job', '')}` "
        f"({entry.get('model', 'unknown') or 'model unavailable'}) "
        f"— {entry.get('status', 'unknown')}"
        for entry in review.provenance
    )
    lines.append("")
    return lines


def _render_second_opinion_lines(review: DependencyReview) -> list[str]:
    """Render the second-opinion status and any blocking findings."""
    second_opinion = review.second_opinion
    if second_opinion is None:
        return []
    lines = ["## Second opinion", ""]
    if second_opinion.status == "failed":
        lines.append(f"- Failed: {second_opinion.error or 'unknown error'}")
    else:
        lines.extend(_render_completed_second_opinion_lines(second_opinion))
    lines.append("")
    return lines


def _render_completed_second_opinion_lines(record: DependencySecondOpinionRecord) -> list[str]:
    """Render a completed second opinion with advisory and blocking findings."""
    lines = [f"- Job: `{record.job}`"]
    if record.model:
        lines.append(f"- Model: `{record.model}`")
    if record.summary:
        lines.append(f"- Summary: {record.summary}")
    blocking_findings = _collect_blocking_findings(record)
    lines.append(f"- Advisory findings retained: {len(record.findings) - len(blocking_findings)}")
    if blocking_findings:
        lines.append("- Blocking findings:")
        lines.extend(_render_blocking_finding(finding) for finding in blocking_findings)
    else:
        lines.append("- Blocking findings: none")
    return lines


def _collect_blocking_findings(record: DependencySecondOpinionRecord) -> list[DependencySecondOpinionFinding]:
    """Select findings that stop automatic dependency promotion."""
    return [
        finding
        for finding in record.findings
        if finding.severity in {"P0", "P1"} or finding.recommendation == "needs_human_review"
    ]


def _render_blocking_finding(finding: DependencySecondOpinionFinding) -> str:
    """Render one blocking finding as an indented operator bullet."""
    return (
        "  - "
        f"{finding.severity} `{finding.prerequisite_id} -> {finding.dependent_id}`: "
        f"{finding.recommendation} — {finding.rationale}"
    )


def _render_review_file_lines() -> list[str]:
    """Render the scratch files section shared by every review summary."""
    return [
        "## Files",
        "",
        "- `review.yaml`: complete structured proposal and provenance",
        "- `README.md`: this summary; do not edit it as a source of truth",
        "",
    ]


def _failed_review(
    *,
    run_id: str,
    repo_root: Path,
    source_path: Path,
    catalog: Mapping[str, Any],
    errors: list[str],
    provenance: list[dict[str, object]],
) -> DependencyReview:
    """Build a visible failed review without substituting a fake model."""
    return DependencyReview(
        run_id=run_id,
        source_catalog=_relative_path(source_path, repo_root),
        source_catalog_hash=_content_hash(catalog),
        source_snapshot=str(catalog.get("snapshot", "")),
        catalog_schema_version=_schema_version(catalog),
        status="failed",
        owner_count=len(catalog.get("entries", []) if isinstance(catalog.get("entries"), list) else []),
        reviewed_owner_count=0,
        unresolved=[],
        validation_errors=sorted(set(errors)),
        provenance=provenance,
    )


def _load_mapping(path: Path) -> dict[str, Any]:
    """Read one YAML mapping."""
    if not path.is_file():
        raise FileNotFoundError(f"dependency input not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"dependency input must be a YAML mapping: {path}")
    return payload


def _schema_version(catalog: Mapping[str, Any]) -> int:
    """Normalize the catalog schema version for scratch provenance."""
    try:
        return int(catalog.get("schema_version", 0))
    except (TypeError, ValueError):
        return 0


def _relative_path(path: Path, root: Path) -> str:
    """Render a stable relative path when the source belongs to the repository."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _content_hash(payload: Mapping[str, Any]) -> str:
    """Hash normalized catalog content."""
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "DependencyProposer",
    "DependencySecondOpinionReviewer",
    "build_dependency_prompt",
    "build_second_opinion_prompt",
    "rerun_dependency_second_opinion",
    "run_dependency_design",
]
