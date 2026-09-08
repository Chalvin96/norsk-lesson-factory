"""Entry point: ``normalize_candidates_node`` (registered as ``normalize_candidates``).

``build_catalog_graph`` calls this node after both discovery branches to assign
stable provenance ids and normalize slugs, titles, and aliases before any
comparison or semantic call.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.catalog.services.normalization import normalize_text
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.nodes.node_support import candidate_list
from lesson_builder.workflow.catalog_design.nodes.node_support import request_from_state
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def normalize_candidates_node(state: CatalogState, _deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Assign stable provenance ids and normalize slugs before comparison."""
    request = request_from_state(state)
    source_candidates = [
        ("explorer", candidate_list(state.get("explorer_candidates", []))),
        ("reviewer", candidate_list(state.get("reviewer_candidates", []))),
    ]
    normalized: list[CatalogCandidate] = []
    used_ids: set[str] = set()
    for source, candidates in source_candidates:
        for candidate in candidates:
            base_slug = normalize_slug(candidate.slug) or normalize_slug(candidate.title) or "catalog_owner"
            candidate_id = f"{source}:{base_slug}"
            suffix = 2
            while candidate_id in used_ids:
                candidate_id = f"{source}:{base_slug}-{suffix}"
                suffix += 1
            used_ids.add(candidate_id)
            updates: dict[str, Any] = {
                "candidate_id": candidate_id,
                "slug": base_slug,
                "title": _normalize_title(candidate.title),
                "alternate_labels": _normalize_aliases(candidate.alternate_labels),
                "category": candidate.category.strip(),
                "source_agent": source,
            }
            if not candidate.cefr_tags and request.cefr_tags:
                updates["cefr_tags"] = request.cefr_tags
            normalized.append(candidate.model_copy(update=updates))
    return {"candidates": [candidate.model_dump(mode="json") for candidate in normalized]}


def _normalize_title(value: str) -> str:
    """Normalize punctuation that makes learner-facing titles harder to scan."""
    normalized = " ".join(value.replace("“", '"').replace("”", '"').split())
    return normalized.replace(":", " - ")


def _normalize_aliases(values: Iterable[str]) -> list[str]:
    """Deduplicate aliases without changing the first human-readable label."""
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        label = " ".join(str(value).split()).strip()
        key = normalize_text(label)
        if not key or key in seen:
            continue
        seen.add(key)
        aliases.append(label)
    return aliases


__all__ = ["normalize_candidates_node"]
