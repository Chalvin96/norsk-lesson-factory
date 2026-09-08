"""Not a check itself — catalog-owned ordering and policy facts.

These constants describe how catalog kinds and CEFR levels order, plus the
dependency-review schema and model-call limits owned by the catalog context.
Repository paths are resolved by ``workspace.paths``.
"""

from __future__ import annotations

K_CATALOG_PLANNER_KIND_ORDER: tuple[str, ...] = (
    "grammar",
    "phraseology",
    "pronunciation",
    "communicative",
    "writing",
)
K_CATALOG_PLANNER_CEFR_ORDER: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")

K_CATALOG_DEPENDENCY_REVIEW_SCHEMA_VERSION = "0.1-catalog-dependency-review"
K_CATALOG_DEPENDENCY_SECOND_REVIEW_TEXT_LIMIT = 160
K_CATALOG_DEPENDENCY_SECOND_REVIEW_EDGE_BATCH_SIZE = 20
K_CATALOG_DEPENDENCY_SECOND_REVIEW_MAX_WORKERS = 4
K_CATALOG_DEPENDENCY_SECOND_REVIEW_RETRIES = 1

__all__ = [
    "K_CATALOG_PLANNER_KIND_ORDER",
    "K_CATALOG_PLANNER_CEFR_ORDER",
    "K_CATALOG_DEPENDENCY_REVIEW_SCHEMA_VERSION",
    "K_CATALOG_DEPENDENCY_SECOND_REVIEW_TEXT_LIMIT",
    "K_CATALOG_DEPENDENCY_SECOND_REVIEW_EDGE_BATCH_SIZE",
    "K_CATALOG_DEPENDENCY_SECOND_REVIEW_MAX_WORKERS",
    "K_CATALOG_DEPENDENCY_SECOND_REVIEW_RETRIES",
]
