"""Not a check itself — catalog-design graph version and quality policy.

These constants own the catalog-design graph identity and the thresholds its
evaluation and reconciliation nodes enforce on proposals.
"""

from __future__ import annotations

# ── Catalog design graph and quality policy ────────────────────────────────

K_CATALOG_DESIGN_GRAPH_VERSION = "v2"
K_CATALOG_MIN_QUALITY_SCORE = 0.70
K_CATALOG_MIN_DIMENSION_SCORE = 0.55
K_CATALOG_MIN_RESOLUTION_CONFIDENCE = 0.65
K_CATALOG_QUALITY_DIMENSIONS = (
    "distinctness",
    "usefulness",
    "scope_clarity",
    "category_fit",
    "accuracy",
)
K_CATALOG_TITLE_BANNED_TERMS = (
    "dummy",
    "expletive",
    "presentational",
    "ambient",
    "interrogative",
    "constituent",
    "threshold",
    "valency",
    "anaphora",
)
K_CATALOG_MERGE_GENERIC_TOKENS = frozenset(
    {
        "and",
        "choose",
        "decide",
        "form",
        "for",
        "how",
        "learn",
        "meaning",
        "norwegian",
        "say",
        "sentence",
        "talk",
        "the",
        "use",
        "when",
        "with",
    }
)

__all__ = [
    "K_CATALOG_DESIGN_GRAPH_VERSION",
    "K_CATALOG_MIN_QUALITY_SCORE",
    "K_CATALOG_MIN_DIMENSION_SCORE",
    "K_CATALOG_MIN_RESOLUTION_CONFIDENCE",
    "K_CATALOG_QUALITY_DIMENSIONS",
    "K_CATALOG_TITLE_BANNED_TERMS",
    "K_CATALOG_MERGE_GENERIC_TOKENS",
]
