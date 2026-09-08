"""Entry point: `apply_dependency_review` applies a human-approved graph."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.domain.catalog.models import DependencyEdge
from lesson_builder.domain.catalog.models import DependencyPromotionResult
from lesson_builder.domain.catalog.models import DependencyReview
from lesson_builder.domain.catalog.models import DependencySecondOpinionFinding
from lesson_builder.domain.catalog.services.validate_dependency_graph import validate_dependency_graph
from lesson_builder.domain.catalog.settings import K_CATALOG_PLANNER_CEFR_ORDER
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths


def apply_dependency_review(
    *,
    repo_root: Path,
    review_path: Path,
    approval_path: Path,
    catalog_path: Path | None = None,
) -> DependencyPromotionResult:
    """Promote one explicitly approved dependency review atomically."""
    root = Path(repo_root)
    review_file = _resolve_path(root, review_path)
    approval_file = _resolve_path(root, approval_path)
    catalog_file = _resolve_path(root, catalog_path) if catalog_path is not None else WorkspacePaths(root).catalog_file
    review = DependencyReview.model_validate(_load_mapping(review_file))
    approval = _load_mapping(approval_file)
    catalog = _load_mapping(catalog_file)

    _require_approval(approval, review, catalog, root, approval_file)
    edges = _approved_edges(review, approval)
    owner_ids = [str(entry.get("id", "")) for entry in _entries(catalog)]
    cefr_by_owner = {owner_id: _cefr_floor(entry) for owner_id, entry in zip(owner_ids, _entries(catalog), strict=True)}
    validation_errors = validate_dependency_graph(owner_ids, edges, cefr_by_owner=cefr_by_owner)
    if validation_errors:
        raise ValueError("approved dependency graph is invalid: " + "; ".join(validation_errors))

    updated = _promoted_catalog(catalog, review, approval, edges)
    encoded = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False)
    write_text_atomically(catalog_file, encoded)
    catalog_hash = _content_hash(updated)
    return DependencyPromotionResult(
        review_run=review.run_id,
        approval_path=str(approval_file.relative_to(root)),
        catalog_path=str(catalog_file.relative_to(root)),
        catalog_hash=catalog_hash,
        owner_count=len(owner_ids),
        required_edge_count=sum(edge.kind == "required" for edge in edges),
        helpful_edge_count=sum(edge.kind == "helpful" for edge in edges),
    )


def _require_approval(
    approval: Mapping[str, Any],
    review: DependencyReview,
    catalog: Mapping[str, Any],
    root: Path,
    approval_file: Path,
) -> None:
    """Reject stale, implicit, or incomplete human approval."""
    if approval.get("status") != "approved":
        raise ValueError("dependency approval ledger must have status=approved")
    if str(approval.get("approved_by", "")).strip().lower() != "human":
        raise ValueError("dependency approval ledger requires approved_by=human")
    if str(approval.get("review_run", "")) != review.run_id:
        raise ValueError(f"dependency approval targets a different review: {approval_file}")
    if str(approval.get("source_catalog_hash", "")) != review.source_catalog_hash:
        raise ValueError("dependency approval does not match the review catalog hash")
    if _content_hash(catalog) != review.source_catalog_hash:
        raise ValueError("catalog changed after dependency review; promotion is stale")
    decisions = approval.get("decisions")
    if not isinstance(decisions, list):
        raise TypeError("dependency approval ledger requires a decisions list")
    unresolved = set(review.unresolved)
    allowed = _allowed_unresolved(review)
    unexpected = sorted(unresolved - allowed)
    if unexpected:
        raise ValueError("dependency review has unresolved items outside the approval ledger: " + "; ".join(unexpected))
    if review.second_opinion is None or review.second_opinion.status != "completed":
        raise ValueError("dependency review requires a completed second opinion before promotion")


def _allowed_unresolved(review: DependencyReview) -> set[str]:
    """Return only the unresolved items the approval ledger can account for."""
    allowed: set[str] = set()
    second_opinion = review.second_opinion
    if second_opinion is not None:
        allowed.update(
            _severity_unresolved_label(finding)
            for finding in second_opinion.findings
            if finding.severity in {"P0", "P1"}
        )
    return allowed


def _severity_unresolved_label(finding: DependencySecondOpinionFinding) -> str:
    """Rebuild the runner's unresolved label for one P0/P1 finding."""
    return (
        f"second-opinion severity requires human review: {finding.severity} "
        f"{finding.prerequisite_id} -> {finding.dependent_id}"
    )


def _severity_unresolved_pairs(review: DependencyReview) -> set[tuple[str, str]]:
    """Extract endpoint pairs the review flags for P0/P1 human decisions."""
    return {
        _parse_severity_unresolved(item)
        for item in review.unresolved
        if item.startswith("second-opinion severity requires human review:")
    }


def _parse_severity_unresolved(item: str) -> tuple[str, str]:
    """Parse one severity unresolved label into its endpoint pair."""
    detail = item.split(": ", 1)[1]
    severity_and_prerequisite, dependent_id = detail.rsplit(" -> ", 1)
    _severity, prerequisite_id = severity_and_prerequisite.split(" ", 1)
    return prerequisite_id, dependent_id


def _severity_review_pairs(review: DependencyReview) -> set[tuple[str, str]]:
    """Return endpoint pairs the completed second opinion itself flags P0/P1."""
    second_opinion = review.second_opinion
    if second_opinion is None:
        return set()
    return {
        (finding.prerequisite_id, finding.dependent_id)
        for finding in second_opinion.findings
        if finding.severity in {"P0", "P1"}
    }


def _approved_edges(review: DependencyReview, approval: Mapping[str, Any]) -> list[DependencyEdge]:
    """Apply only the approval ledger's explicit, second-opinion-backed removals."""
    remove = _collect_removal_keys(approval)
    _validate_removal_keys(remove, review)
    if {(key[0], key[1]) for key in remove} != _severity_unresolved_pairs(review):
        raise ValueError(
            "every second-opinion human-review item requires one explicit removal decision, "
            "and every removal must resolve one such item"
        )
    return [edge for edge in review.proposed_edges if _edge_key(edge.model_dump(mode="json")) not in remove]


def _collect_removal_keys(approval: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    """Return the explicit required-edge removals in an approval ledger."""
    return {_edge_key(item) for item in _decisions(approval) if item.get("action") == "remove_required_edge"}


def _validate_removal_keys(
    remove: set[tuple[str, str, str]],
    review: DependencyReview,
) -> None:
    """Reject removals that are absent, non-required, or unreviewed."""
    proposed_keys = {_edge_key(edge.model_dump(mode="json")) for edge in review.proposed_edges}
    severity_pairs = _severity_review_pairs(review)
    for key in sorted(remove):
        _validate_removal_key(key, proposed_keys, severity_pairs)


def _validate_removal_key(
    key: tuple[str, str, str],
    proposed_keys: set[tuple[str, str, str]],
    severity_pairs: set[tuple[str, str]],
) -> None:
    """Validate one explicit required-edge removal."""
    if key not in proposed_keys:
        raise ValueError(f"approval ledger removes an edge absent from the proposed graph: {key[0]} -> {key[1]}")
    if key[2] != "required":
        raise ValueError(f"approval ledger may only remove required edges: {key[0]} -> {key[1]} ({key[2]})")
    if (key[0], key[1]) not in severity_pairs:
        raise ValueError(
            f"approval ledger removes an edge the second opinion did not flag for human review: {key[0]} -> {key[1]}"
        )


def _promoted_catalog(
    catalog: Mapping[str, Any],
    review: DependencyReview,
    approval: Mapping[str, Any],
    edges: Sequence[DependencyEdge],
) -> dict[str, Any]:
    """Build schema-3 YAML in memory before the atomic replacement."""
    updated = dict(catalog)
    entries = []
    for raw in _entries(catalog):
        entry = dict(raw)
        owner_id = str(entry["id"])
        entry["prerequisites"] = _edge_ids(edges, owner_id, "required")
        entry["helpful_prerequisites"] = _edge_ids(edges, owner_id, "helpful")
        entries.append(entry)
    updated["schema_version"] = 3
    updated["entries"] = entries
    updated["dependency_graph"] = {
        "status": "complete",
        "review_run": review.run_id,
        "source_catalog_hash": review.source_catalog_hash,
        "approved_by": approval["approved_by"],
        "approved_at": approval.get("approved_at", ""),
        "required_edges": [edge.model_dump(mode="json") for edge in edges if edge.kind == "required"],
        "helpful_edges": [edge.model_dump(mode="json") for edge in edges if edge.kind == "helpful"],
    }
    return updated


def _edge_ids(edges: Sequence[DependencyEdge], dependent_id: str, kind: str) -> list[str]:
    """Return stable, de-duplicated prerequisite IDs for one owner."""
    result: list[str] = []
    for edge in edges:
        if edge.dependent_id == dependent_id and edge.kind == kind and edge.prerequisite_id not in result:
            result.append(edge.prerequisite_id)
    return result


def _decisions(approval: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Load explicit approval decision mappings."""
    values = approval.get("decisions")
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise ValueError("dependency approval decisions must be mappings")
    return list(values)


def _edge_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    """Build one edge identity from a YAML mapping."""
    return str(value.get("prerequisite_id", "")), str(value.get("dependent_id", "")), str(value.get("kind", ""))


def _entries(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return validated catalog entry mappings."""
    entries = catalog.get("entries")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("approved catalog entries must be a list of mappings")
    return list(entries)


def _cefr_floor(entry: Mapping[str, Any]) -> str:
    """Return the earliest declared CEFR level for an owner."""
    tags = [str(tag).strip() for tag in entry.get("cefr_tags", []) or []]
    known = [tag for tag in tags if tag in K_CATALOG_PLANNER_CEFR_ORDER]
    if not known:
        raise ValueError(f"catalog entry {entry.get('id', '<unknown>')} has no CEFR tag")
    return min(known, key=K_CATALOG_PLANNER_CEFR_ORDER.index)


def _resolve_path(root: Path, value: Path) -> Path:
    """Resolve a CLI path relative to the repository root."""
    return value if value.is_absolute() else root / value


def _load_mapping(path: Path) -> dict[str, Any]:
    """Load one YAML mapping."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected YAML mapping: {path}")
    return payload


def _content_hash(payload: Mapping[str, Any]) -> str:
    """Hash normalized YAML semantics for revision checks."""
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = ["apply_dependency_review"]
