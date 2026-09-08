"""Not a check itself — curriculum-plan schema and planning policy."""

from __future__ import annotations

K_CATALOG_PLANNER_SCHEMA_VERSION = "0.5-catalog-plan"
K_CATALOG_PLANNER_DEFAULT_ACTIVE_KINDS: tuple[str, ...] = (
    "grammar",
    "phraseology",
    "communicative",
)
# The planner enforces the early-CEFR technical-label budget when it selects
# glossary concepts for a slot. These budgets are planner-owned policy, not
# lesson-workflow configuration.
K_CATALOG_PLANNER_EARLY_CEFR_TAGS: tuple[str, ...] = ("A1", "A2")
K_CATALOG_PLANNER_EARLY_TECHNICAL_LABEL_BUDGET = 3

__all__ = [
    "K_CATALOG_PLANNER_SCHEMA_VERSION",
    "K_CATALOG_PLANNER_DEFAULT_ACTIVE_KINDS",
    "K_CATALOG_PLANNER_EARLY_CEFR_TAGS",
    "K_CATALOG_PLANNER_EARLY_TECHNICAL_LABEL_BUDGET",
]
