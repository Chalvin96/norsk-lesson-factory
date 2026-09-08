"""Entry point: `build_owner_resolution` maps historical IDs to active owners."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lesson_builder.domain.catalog.models import OwnerResolution
from lesson_builder.domain.catalog.models import normalize_owner_reference


def build_owner_resolution(catalog: Mapping[str, Any]) -> OwnerResolution:
    """Build a one-to-one map from source/legacy IDs to active catalog owners."""
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise TypeError("approved catalog entries must be a list")
    active_ids, candidates = _collect_owner_candidates(entries)
    deleted_ids = _deleted_owner_ids(catalog)
    _add_deleted_candidates(candidates, deleted_ids)
    resolved, ambiguous = _resolve_owner_candidates(candidates)

    return OwnerResolution(
        active_ids=frozenset(active_ids),
        resolved=resolved,
        deleted=frozenset(normalize_owner_reference(value) for value in deleted_ids),
        ambiguous=ambiguous,
    )


def _collect_owner_candidates(entries: list[Any]) -> tuple[set[str], dict[str, set[str]]]:
    """Collect active owner IDs and all source aliases that point to them."""
    active_ids: set[str] = set()
    candidates: dict[str, set[str]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("approved catalog entries must be mappings")
        owner_id = _required_string(entry.get("id"), "catalog entry id")
        if owner_id in active_ids:
            raise ValueError(f"duplicate active catalog ID: {owner_id!r}")
        active_ids.add(owner_id)
        _add_candidate(candidates, owner_id, owner_id)
        for metadata_field in ("source_owners", "merged_from", "aliases"):
            values = entry.get(metadata_field) or []
            if not isinstance(values, list):
                raise TypeError(f"catalog field {metadata_field!r} must be a list")
            for value in values:
                if isinstance(value, str) and value.strip():
                    _add_candidate(candidates, value, owner_id)
    return active_ids, candidates


def _deleted_owner_ids(catalog: Mapping[str, Any]) -> set[str]:
    """Collect deleted owner IDs from both supported catalog locations."""
    deleted_ids = {
        value.strip()
        for value in (catalog.get("summary", {}).get("deleted_owner_ids", []) or [])
        if isinstance(value, str) and value.strip()
    }
    deleted_ids.update(
        value.strip()
        for value in (catalog.get("deleted_owner_ids", []) or [])
        if isinstance(value, str) and value.strip()
    )
    return deleted_ids


def _add_deleted_candidates(candidates: dict[str, set[str]], deleted_ids: set[str]) -> None:
    """Reserve deleted references so they cannot resolve to active owners."""
    for value in deleted_ids:
        normalized = normalize_owner_reference(value)
        if normalized not in candidates:
            candidates[normalized] = set()


def _resolve_owner_candidates(
    candidates: dict[str, set[str]],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Split candidate aliases into unique resolutions and ambiguities."""
    resolved: dict[str, str] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    for reference, owners in candidates.items():
        if len(owners) == 1:
            resolved[reference] = next(iter(owners))
        elif len(owners) > 1:
            ambiguous[reference] = tuple(sorted(owners))
    return resolved, ambiguous


def _add_candidate(candidates: dict[str, set[str]], reference: str, owner_id: str) -> None:
    candidates.setdefault(normalize_owner_reference(reference), set()).add(owner_id)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


__all__ = ["build_owner_resolution"]
