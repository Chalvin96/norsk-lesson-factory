"""Not a check itself — catalog and curriculum ordering facts."""

from __future__ import annotations

K_CATALOG_PLANNER_KIND_ORDER: tuple[str, ...] = (
    "grammar",
    "phraseology",
    "pronunciation",
    "communicative",
    "writing",
)
K_CATALOG_PLANNER_CEFR_ORDER: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")

__all__ = [
    "K_CATALOG_PLANNER_KIND_ORDER",
    "K_CATALOG_PLANNER_CEFR_ORDER",
]
