"""Entry points: `normalize_assessments`, `normalize_edges`, `build_dependency_edges`,
`build_owner_payload`, `build_cefr_by_owner`, `find_assessment`, and `normalize_ids`.

Pure catalog dependency policy for the model-assisted design workflow.  This
module maps model references to approved owner IDs, expands assessment
prerequisites into edges, and derives deterministic catalog facts.  Provider
invocation, YAML serialization, scratch paths, and repository writes stay in
the `design_dependencies` orchestration facade, which calls these helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from lesson_builder.domain.catalog.models import DependencyAssessment
from lesson_builder.domain.catalog.models import DependencyEdge
from lesson_builder.domain.catalog.models import OwnerResolution


def normalize_assessments(
    assessments: Sequence[DependencyAssessment],
    resolution: OwnerResolution,
) -> tuple[list[DependencyAssessment], list[str]]:
    """Map model owner references to active IDs and preserve invalid ones."""
    normalized: list[DependencyAssessment] = []
    errors: list[str] = []
    seen: set[str] = set()
    for assessment in assessments:
        owner_id = resolution.resolve(assessment.owner_id)
        if owner_id is None:
            errors.append(f"unresolved assessment owner: {assessment.owner_id}")
            continue
        if owner_id in seen:
            errors.append(f"duplicate dependency assessment: {owner_id}")
            continue
        seen.add(owner_id)
        required, required_errors = normalize_ids(assessment.required_prerequisites, resolution)
        helpful, helpful_errors = normalize_ids(assessment.helpful_prerequisites, resolution)
        errors.extend(required_errors)
        errors.extend(helpful_errors)
        normalized.append(
            assessment.model_copy(
                update={
                    "owner_id": owner_id,
                    "required_prerequisites": required,
                    "helpful_prerequisites": helpful,
                }
            )
        )
    return normalized, errors


def normalize_edges(
    edges: Sequence[DependencyEdge],
    resolution: OwnerResolution,
) -> tuple[list[DependencyEdge], list[str]]:
    """Map model edge endpoints and retain unresolved endpoints as review errors."""
    normalized: list[DependencyEdge] = []
    errors: list[str] = []
    for edge in edges:
        prerequisite_id = resolution.resolve(edge.prerequisite_id)
        dependent_id = resolution.resolve(edge.dependent_id)
        if prerequisite_id is None:
            errors.append(f"unresolved prerequisite endpoint: {edge.prerequisite_id}")
        if dependent_id is None:
            errors.append(f"unresolved dependent endpoint: {edge.dependent_id}")
        if prerequisite_id is None or dependent_id is None:
            continue
        normalized.append(edge.model_copy(update={"prerequisite_id": prerequisite_id, "dependent_id": dependent_id}))
    return normalized, errors


def build_dependency_edges(assessments: Sequence[DependencyAssessment]) -> list[DependencyEdge]:
    """Expand owner assessments into directed edges when the reviewer omits its edge list."""
    edges: list[DependencyEdge] = []
    for assessment in assessments:
        for prerequisite_id in assessment.required_prerequisites:
            edges.append(
                DependencyEdge(
                    prerequisite_id=prerequisite_id,
                    dependent_id=assessment.owner_id,
                    kind="required",
                    confidence=0.7,
                    rationale=assessment.rationale,
                    evidence=assessment.evidence,
                )
            )
        for prerequisite_id in assessment.helpful_prerequisites:
            edges.append(
                DependencyEdge(
                    prerequisite_id=prerequisite_id,
                    dependent_id=assessment.owner_id,
                    kind="helpful",
                    confidence=0.7,
                    rationale=assessment.rationale,
                    evidence=assessment.evidence,
                )
            )
    return edges


def build_owner_payload(catalog: Mapping[str, Any]) -> list[dict[str, object]]:
    """Render only final owner facts required by dependency design."""
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise TypeError("approved catalog entries must be a list")
    payload: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("approved catalog entries must be mappings")
        payload.append(
            {
                "id": entry.get("id"),
                "catalog_kind": entry.get("catalog_kind"),
                "family_id": entry.get("family_id"),
                "title": entry.get("title"),
                "learner_outcome": entry.get("learner_outcome"),
                "teaching_points": entry.get("teaching_points", []),
                "cefr_tags": entry.get("cefr_tags", []),
                "source_owners": entry.get("source_owners", []),
                "merged_from": entry.get("merged_from", []),
            }
        )
    return payload


def build_cefr_by_owner(catalog: Mapping[str, Any]) -> dict[str, str]:
    """Return the earliest declared CEFR floor for each active owner."""
    levels = ("A1", "A2", "B1", "B2", "C1", "C2")
    positions = {level: index for index, level in enumerate(levels)}
    result: dict[str, str] = {}
    for entry in catalog.get("entries", []):
        if not isinstance(entry, Mapping):
            continue
        owner_id = str(entry.get("id", ""))
        tags = [str(tag).strip() for tag in entry.get("cefr_tags", []) or []]
        known = [tag for tag in tags if tag in positions]
        if known:
            result[owner_id] = min(known, key=positions.__getitem__)
        else:
            result[owner_id] = ""
    return result


def find_assessment(assessments: Sequence[DependencyAssessment], owner_id: str) -> DependencyAssessment | None:
    """Find one normalized assessment by active owner ID."""
    return next((assessment for assessment in assessments if assessment.owner_id == owner_id), None)


def normalize_ids(values: Sequence[str], resolution: OwnerResolution) -> tuple[list[str], list[str]]:
    """Normalize a list of model owner references."""
    normalized: list[str] = []
    errors: list[str] = []
    for value in values:
        owner_id = resolution.resolve(value)
        if owner_id is None:
            errors.append(f"unresolved prerequisite reference: {value}")
        elif owner_id not in normalized:
            normalized.append(owner_id)
    return normalized, errors


__all__ = [
    "build_owner_payload",
    "build_cefr_by_owner",
    "build_dependency_edges",
    "find_assessment",
    "normalize_assessments",
    "normalize_edges",
    "normalize_ids",
]
