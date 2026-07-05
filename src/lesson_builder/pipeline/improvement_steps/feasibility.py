"""Entry point: ``feasibility_check``.

Deterministic-first pre-flight that gates the expensive author call. Runs
BEFORE ``add_exercises`` to avoid wasting an LLM call on infeasible requests.

Routes: ``feasible`` -> author | ``off_scope`` -> triage | ``infeasible`` -> human.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec

FeasibilityVerdict = Literal["feasible", "off_scope", "infeasible"]


class FeasibilityResult(BaseModel):
    verdict: FeasibilityVerdict
    reason: str
    spec: ImprovementSpec


def feasibility_check(
    spec: ImprovementSpec,
    *,
    lesson: dict[str, Any] | None = None,
    requirements: dict[str, Any] | None = None,
    known_slugs: list[str] | None = None,
) -> FeasibilityResult:
    """Pre-flight feasibility gate (cheap, mostly deterministic).

    - bloom mode: requested bloom level in an objective's bloom_targets?
    - content mode: target content is non-empty and in lesson scope?
    - volume mode: not already saturated?
    - target_slug: known to the repo?
    """
    if spec.target_slug and known_slugs and spec.target_slug not in known_slugs:
        return FeasibilityResult(
            verdict="off_scope",
            reason=f"slug {spec.target_slug!r} not found in repo; may be a new topic",
            spec=spec,
        )

    if spec.operation == "add_exercises" and spec.bloom_level and lesson:
        bloom_ok = _check_bloom_feasibility(spec, lesson)
        if not bloom_ok:
            return FeasibilityResult(
                verdict="infeasible",
                reason=(
                    f"bloom level {spec.bloom_level!r} not in any objective's bloom_targets; "
                    "a requirements change is needed (never auto-mutate curriculum)"
                ),
                spec=spec,
            )

    if spec.operation == "add_exercises" and lesson:
        saturated = _check_saturation(spec, lesson)
        if saturated:
            return FeasibilityResult(
                verdict="off_scope",
                reason=f"lesson already has {saturated} exercises for this objective; redundant",
                spec=spec,
            )

    if not spec.target_slug:
        return FeasibilityResult(
            verdict="off_scope",
            reason="no target slug identified; route to triage for placement",
            spec=spec,
        )

    return FeasibilityResult(
        verdict="feasible",
        reason=f"{spec.operation} on {spec.target_slug} with count={spec.count}",
        spec=spec,
    )


def _check_bloom_feasibility(spec: ImprovementSpec, lesson: dict[str, Any]) -> bool:
    """Check if the requested bloom level is in any objective's bloom_targets."""
    if not spec.bloom_level:
        return True
    return any(spec.bloom_level in obj.get("bloom_targets", []) for obj in lesson.get("objectives", []))


def _check_saturation(spec: ImprovementSpec, lesson: dict[str, Any]) -> int | None:
    """Return the exercise count for the target objective if saturated (>= 8)."""
    objective_id = spec.objective_id
    exercises = [
        el
        for el in lesson.get("elements", [])
        if el.get("element_kind") == "exercise"
        and (not objective_id or el.get("objective_id") == objective_id)
    ]
    if len(exercises) >= 8:
        return len(exercises)
    return None


__all__ = [
    "FeasibilityResult",
    "FeasibilityVerdict",
    "feasibility_check",
]
