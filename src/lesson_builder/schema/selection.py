"""Deterministic Bloom->operation selection. The LLM never picks operations."""

from __future__ import annotations

MIN_EXERCISES_PER_OBJECTIVE = 2

_MATRIX: dict[str, tuple[str, ...]] = {
    "remember": ("recall_fill", "match_pairs"),
    "understand": ("judge", "choose", "recall_fill"),
    "apply": ("build", "recall_fill"),
    "analyze": ("find_fix", "categorize", "choose"),
}


def eligible_operations(bloom_targets: list[str]) -> list[str]:
    """Union of operations eligible for the given Bloom targets, order-stable, de-duped."""
    seen: dict[str, None] = {}
    for target in bloom_targets:
        for op in _MATRIX.get(target, ()):
            seen.setdefault(op, None)
    return list(seen)
