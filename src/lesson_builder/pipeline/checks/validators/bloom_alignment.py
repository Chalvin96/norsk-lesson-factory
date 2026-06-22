"""Entry point: ``bloom_alignment_check``.

Deterministic Bloom-level alignment check.

Flags two pedagogical-coverage defects the schema cannot enforce on its own:

1. An exercise whose ``bloom_level`` is not among its linked objective's
   ``bloom_targets`` (the exercise is tagged with a cognitive level the objective
   never claimed to reach).
2. An objective whose exercises never reach any of its declared ``bloom_targets``
   (the lesson claims a target but only practices below it).

The eligible-operation matrix in :mod:`lesson_builder.schema.selection` already
constrains *operations* per Bloom level; this check constrains the *declared*
``bloom_level`` of each exercise against its objective's targets.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult


def bloom_alignment_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag exercises off their objective's bloom targets and objectives that never reach target."""
    results: list[CheckResult] = []
    objectives = _objectives_by_id(data)

    # 1. Per-exercise bloom_level must be in the linked objective's bloom_targets.
    reached: dict[str, set[str]] = {oid: set() for oid in objectives}
    for ex in _exercises(data):
        ex_id = str(ex.get("id", ""))
        bloom = ex.get("bloom_level")
        obj_id = ex.get("objective_id")
        if not bloom or not obj_id:
            continue
        reached.setdefault(obj_id, set()).add(bloom)
        obj = objectives.get(obj_id)
        if obj is None:
            # Dangling objective_id is reported by objective_structural, not here.
            continue
        targets = obj.get("bloom_targets") or []
        if bloom not in targets:
            results.append(
                CheckResult(
                    check_id="bloom_alignment",
                    severity="warning",
                    unit_id=ex_id,
                    message=(
                        f"exercise bloom_level '{bloom}' not in objective '{obj_id}' "
                        f"targets {targets}"
                    ),
                    fix_hint=(
                        f"Re-tag the exercise with one of {targets}, or revise the objective's "
                        "bloom_targets to include this level."
                    ),
                )
            )

    # 2. Each objective should be reached by at least one exercise at each declared
    #    target level. Missing levels signal the lesson claims a target it never practices.
    for oid, obj in objectives.items():
        reached_levels = reached.get(oid, set())
        if not reached_levels:
            # No exercises at all reaches this objective; section 3 below reports
            # that case so it isn't also double-reported as "unreached targets" here.
            continue
        targets = set(obj.get("bloom_targets") or [])
        unreached = sorted(targets - reached_levels)
        if unreached:
            results.append(
                CheckResult(
                    check_id="bloom_alignment",
                    severity="warning",
                    unit_id=oid,
                    message=(
                        f"objective '{oid}' targets {sorted(targets)} but exercises only "
                        f"reach {sorted(reached_levels)}; unreached: {unreached}"
                    ),
                    fix_hint=(
                        f"Add at least one exercise at {unreached} for objective '{oid}', "
                        "or drop the unreached target."
                    ),
                )
            )

    results.extend(_objectives_without_linked_exercises(objectives, reached))
    return results


def _objectives_without_linked_exercises(
    objectives: dict[str, dict[str, Any]],
    reached: dict[str, set[str]],
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for oid in objectives:
        if reached.get(oid):
            continue
        results.append(
            CheckResult(
                check_id="bloom_alignment",
                severity="warning",
                unit_id=oid,
                message=f"objective '{oid}' has no linked exercises",
                fix_hint=f"Add at least one exercise tagged with objective_id '{oid}'.",
            )
        )
    return results


def _objectives_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        obj["id"]: obj
        for obj in data.get("objectives", [])
        if isinstance(obj, dict) and obj.get("id")
    }


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "exercise"
    ]
