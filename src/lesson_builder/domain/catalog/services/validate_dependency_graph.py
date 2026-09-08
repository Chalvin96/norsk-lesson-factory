"""Entry point: `validate_dependency_graph` checks edges and computes order."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from functools import partial

from lesson_builder.domain.catalog.models import DependencyEdge

K_CEFR_ORDER: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")


def validate_dependency_graph(
    owner_ids: Iterable[str],
    edges: Iterable[DependencyEdge],
    *,
    cefr_by_owner: Mapping[str, str] | None = None,
) -> list[str]:
    """Return deterministic validation errors for one dependency graph."""
    owners = set(owner_ids)
    materialized = list(edges)
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    required_pairs: set[tuple[str, str]] = set()
    helpful_pairs: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = defaultdict(list)

    _collect_edge_constraints(
        materialized,
        owners,
        errors,
        seen,
        required_pairs,
        helpful_pairs,
        adjacency,
    )
    errors.extend(_conflicting_pair_errors(required_pairs, helpful_pairs))

    if cefr_by_owner is not None:
        errors.extend(_cefr_inversion_errors(required_pairs, cefr_by_owner))

    cycle = _find_cycle(owners, adjacency)
    if cycle:
        errors.append("required dependency cycle: " + " -> ".join(cycle))
    return errors


def topological_order(
    owner_ids: Iterable[str],
    edges: Iterable[DependencyEdge],
    *,
    cefr_by_owner: Mapping[str, str],
    kind_by_owner: Mapping[str, str],
    kind_order: Sequence[str],
    preferred_rank: Mapping[str, int] | None = None,
) -> list[str]:
    """Return a stable order honoring required edges and deterministic ties.

    ``preferred_rank`` is an optional editorial tie-breaker. Callers validate
    that the ranked owners are eligible before passing it; required edges still
    take precedence because only currently-ready owners are considered.
    """
    materialized = list(edges)
    errors = validate_dependency_graph(owner_ids, materialized, cefr_by_owner=cefr_by_owner)
    if errors:
        raise ValueError("invalid dependency graph: " + "; ".join(errors))

    owners = set(owner_ids)
    required = [edge for edge in materialized if edge.kind == "required"]
    adjacency: dict[str, set[str]] = {owner_id: set() for owner_id in owners}
    indegree = dict.fromkeys(owners, 0)
    for edge in required:
        if edge.dependent_id not in adjacency[edge.prerequisite_id]:
            adjacency[edge.prerequisite_id].add(edge.dependent_id)
            indegree[edge.dependent_id] += 1

    kind_positions = {kind: index for index, kind in enumerate(kind_order)}
    priority = partial(
        _owner_priority,
        cefr_by_owner=cefr_by_owner,
        kind_by_owner=kind_by_owner,
        kind_positions=kind_positions,
        preferred_rank=preferred_rank,
        kind_order=kind_order,
    )

    ready = [owner_id for owner_id, degree in indegree.items() if degree == 0]
    ordered: list[str] = []
    while ready:
        ready.sort(key=priority)
        owner_id = ready.pop(0)
        ordered.append(owner_id)
        for dependent_id in sorted(adjacency[owner_id]):
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)
    if len(ordered) != len(owners):
        raise ValueError("invalid dependency graph: required dependency cycle")
    return ordered


def _owner_priority(
    owner_id: str,
    *,
    cefr_by_owner: Mapping[str, str],
    kind_by_owner: Mapping[str, str],
    kind_positions: Mapping[str, int],
    preferred_rank: Mapping[str, int] | None,
    kind_order: Sequence[str],
) -> tuple[int, int, int, int, str]:
    """Return the deterministic ready-node priority with optional A1 preference."""
    cefr = cefr_by_owner.get(owner_id, "")
    cefr_position = K_CEFR_ORDER.index(cefr) if cefr in K_CEFR_ORDER else len(K_CEFR_ORDER)
    ranks = preferred_rank or {}
    preference_group = int(owner_id not in ranks)
    preference_position = ranks.get(owner_id, 0)
    kind_position = kind_positions.get(kind_by_owner.get(owner_id, ""), len(kind_order))
    return cefr_position, preference_group, preference_position, kind_position, owner_id


def _collect_edge_constraints(
    edges: list[DependencyEdge],
    owners: set[str],
    errors: list[str],
    seen: set[tuple[str, str, str]],
    required_pairs: set[tuple[str, str]],
    helpful_pairs: set[tuple[str, str]],
    adjacency: dict[str, list[str]],
) -> None:
    """Validate edge endpoints and collect graph constraints."""
    for edge in edges:
        pair = (edge.prerequisite_id, edge.dependent_id)
        identity = (*pair, edge.kind)
        if edge.prerequisite_id not in owners:
            errors.append(f"unknown prerequisite owner: {edge.prerequisite_id}")
        if edge.dependent_id not in owners:
            errors.append(f"unknown dependent owner: {edge.dependent_id}")
        if edge.prerequisite_id == edge.dependent_id:
            errors.append(f"self dependency: {edge.prerequisite_id}")
        if identity in seen:
            errors.append(f"duplicate dependency edge: {edge.kind} {edge.prerequisite_id} -> {edge.dependent_id}")
        seen.add(identity)
        if edge.kind == "required":
            required_pairs.add(pair)
            if edge.prerequisite_id in owners and edge.dependent_id in owners:
                adjacency[edge.prerequisite_id].append(edge.dependent_id)
        else:
            helpful_pairs.add(pair)


def _conflicting_pair_errors(required_pairs: set[tuple[str, str]], helpful_pairs: set[tuple[str, str]]) -> list[str]:
    """Report pairs declared with both required and helpful edges."""
    return [
        f"dependency is both required and helpful: {pair[0]} -> {pair[1]}"
        for pair in sorted(required_pairs & helpful_pairs)
    ]


def _cefr_inversion_errors(required_pairs: set[tuple[str, str]], cefr_by_owner: Mapping[str, str]) -> list[str]:
    """Report required edges whose CEFR order runs backwards."""
    errors: list[str] = []
    for prerequisite_id, dependent_id in sorted(required_pairs):
        prerequisite_level = cefr_by_owner.get(prerequisite_id)
        dependent_level = cefr_by_owner.get(dependent_id)
        if prerequisite_level not in K_CEFR_ORDER or dependent_level not in K_CEFR_ORDER:
            continue
        if K_CEFR_ORDER.index(prerequisite_level) > K_CEFR_ORDER.index(dependent_level):
            errors.append(
                f"CEFR inversion: {prerequisite_id} ({prerequisite_level}) -> {dependent_id} ({dependent_level})"
            )
    return errors


def _find_cycle(owners: set[str], adjacency: Mapping[str, Sequence[str]]) -> list[str]:
    state: dict[str, int] = dict.fromkeys(owners, 0)
    trail: list[str] = []

    def visit(owner_id: str) -> list[str]:
        state[owner_id] = 1
        trail.append(owner_id)
        for dependent_id in sorted(adjacency.get(owner_id, ())):
            if state[dependent_id] == 0:
                cycle = visit(dependent_id)
                if cycle:
                    return cycle
            elif state[dependent_id] == 1:
                start = trail.index(dependent_id)
                return [*trail[start:], dependent_id]
        trail.pop()
        state[owner_id] = 2
        return []

    for owner_id in sorted(owners):
        if state[owner_id] == 0:
            cycle = visit(owner_id)
            if cycle:
                return cycle
    return []


__all__ = ["K_CEFR_ORDER", "topological_order", "validate_dependency_graph"]
