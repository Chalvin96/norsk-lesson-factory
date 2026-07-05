"""Entry point: ``compute_naturalness_floors`` / ``naturalness_floor_check`` /
``NaturalnessFloors`` + ``load_naturalness_regression_seeds``.

Corpus-free calibration for the naturalness judge, mirroring the pedagogy
``rubric_floors`` design: derive a per-axis quality FLOOR from the reviewer's own
scores on the GOLDEN lessons, then flag any lesson scoring below a floor on any
naturalness axis (idiomatic_phrasing / register_appropriateness /
terminology_consistency).

Phase-8 status — DEFER-WITH-SCAFFOLD. The idiomatic markers this guards
(``hører seg`` without ``selv``, ``kjenner seg`` = feel-not-know, ``stenger
seks`` without ``klokka``) need native judgment; they are NOT deterministic. This
module lands the derivation hook + the regression-seed loader so the floors CAN be
computed once a naturalness reviewer is trusted, but nothing here is wired into
``_DETERMINISTIC_GATE`` — naturalness stays advisory + human-gated until the floor
is explicitly promoted (``promote=True``), exactly like the pedagogy floors were.

The regression seeds (``data/calibration/naturalness_regression_seeds.json``) are
real bad→good pairs caught by human review this session, kept as evals so the
reviewer's idiomatic-marker sensitivity can be measured before promotion.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.checks.validators.naturalness import (
    K_NATURALNESS_AXES,
    NaturalnessReview,
    naturalness_percent,
)

K_NATURALNESS_FLOORS_RELATIVE_PATH = Path("data") / "calibration" / "naturalness_floors.json"
K_NATURALNESS_FLOORS_PATH = (
    Path(__file__).resolve().parents[4] / K_NATURALNESS_FLOORS_RELATIVE_PATH
)

K_NATURALNESS_SEEDS_RELATIVE_PATH = (
    Path("data") / "calibration" / "naturalness_regression_seeds.json"
)
K_NATURALNESS_SEEDS_PATH = (
    Path(__file__).resolve().parents[4] / K_NATURALNESS_SEEDS_RELATIVE_PATH
)

# A golden scoring below this overall percent is too weak to define a quality floor.
K_DEFAULT_EXCLUDE_BELOW = 60.0

# Subtracted from each axis's golden minimum: gate quality collapse, not a 1-point dip.
K_DEFAULT_FLOOR_TOLERANCE = 1

# A naturalness reviewer over a lesson -> a validated review (or None on backend miss).
NaturalnessScorer = Callable[[dict[str, Any]], NaturalnessReview | None]
NaturalnessPromotion = bool | Literal["idiomatic_phrasing"] | tuple[str, ...]


class NaturalnessRegressionSeed(BaseModel):
    """One real idiomatic-marker regression pair (bad form + its correction).

    A 'bad' text should score LOW on the ``idiomatic_phrasing`` axis; 'good'
    should score high. ``intended_meaning`` keeps semantic idioms from being
    judged as isolated grammatical strings, and ``promotion_candidate`` marks
    whether the pair is strong enough to count toward load-bearing floor
    promotion. Sourced from a human-review fix commit, never fabricated.
    """

    marker: str
    slug: str
    bad: str
    good: str
    intended_meaning: str
    promotion_candidate: bool
    note: str = ""
    source_commit: str = ""


class NaturalnessFloors(BaseModel):
    """Per-axis naturalness quality floors derived from the golden corpus."""

    model: str = "unknown"
    n_goldens: int = 0
    n_in_benchmark: int = 0
    excluded_low: list[str] = Field(default_factory=list)
    overall_min: float = 0.0
    overall_mean: float = 0.0
    axis_floor: dict[str, int] = Field(default_factory=dict)
    per_golden: dict[str, dict[str, Any]] = Field(default_factory=dict)
    load_bearing: bool = False
    load_bearing_axes: list[str] = Field(default_factory=list)

    @field_validator("load_bearing_axes")
    @classmethod
    def _validate_load_bearing_axes(cls, promoted_axes: list[str]) -> list[str]:
        invalid_axes = [axis for axis in promoted_axes if axis not in K_NATURALNESS_AXES]
        if invalid_axes:
            raise ValueError(
                f"unknown naturalness axis/axes for promotion: {sorted(set(invalid_axes))}"
            )
        return sorted(set(promoted_axes))

    def floor_for(self, axis: str) -> int:
        return self.axis_floor.get(axis, 0)

    def is_axis_load_bearing(self, axis: str) -> bool:
        if self.load_bearing:
            return True
        return axis in self.load_bearing_axes


def compute_naturalness_floors(
    goldens: dict[str, dict[str, Any]],
    scorer: NaturalnessScorer,
    *,
    model: str = "unknown",
    exclude_below: float = K_DEFAULT_EXCLUDE_BELOW,
    floor_tolerance: int = K_DEFAULT_FLOOR_TOLERANCE,
    promote: NaturalnessPromotion = False,
) -> NaturalnessFloors:
    """Derive per-axis naturalness floors from the reviewer's golden scores.

    Mirrors ``compute_rubric_floors``: goldens the scorer cannot review, or that
    score below ``exclude_below`` overall, are excluded and listed. ``floor_tolerance``
    is subtracted from each axis's golden minimum so the floor blocks collapse, not
    a single-point dip. Raises ``ValueError`` if no golden survives.
    """
    per_golden: dict[str, dict[str, Any]] = {}
    excluded_low: list[str] = []
    kept: dict[str, NaturalnessReview] = {}

    for slug in sorted(goldens):
        review = scorer(goldens[slug])
        if review is None:
            excluded_low.append(slug)
            per_golden[slug] = {"overall": None, "excluded": True, "reason": "no_review"}
            continue
        overall = naturalness_percent(review)
        axis_scores = {axis: getattr(review.scores, axis) for axis in K_NATURALNESS_AXES}
        entry: dict[str, Any] = {"overall": overall, "scores": axis_scores}
        if overall < exclude_below:
            excluded_low.append(slug)
            entry["excluded"] = True
            entry["reason"] = "below_exclude_threshold"
        else:
            kept[slug] = review
        per_golden[slug] = entry

    if not kept:
        raise ValueError(
            f"no golden cleared the exclude_below={exclude_below} bar; cannot derive "
            f"naturalness floors (reviewed {len(goldens)}, all excluded: {excluded_low})"
        )

    axis_floor = {
        axis: max(0, min(getattr(kept[s].scores, axis) for s in kept) - floor_tolerance)
        for axis in K_NATURALNESS_AXES
    }
    overalls = [naturalness_percent(kept[s]) for s in kept]
    return NaturalnessFloors(
        model=model,
        n_goldens=len(goldens),
        n_in_benchmark=len(kept),
        excluded_low=sorted(excluded_low),
        overall_min=round(min(overalls), 2),
        overall_mean=round(sum(overalls) / len(overalls), 2),
        axis_floor=axis_floor,
        per_golden=per_golden,
        load_bearing=promote is True,
        load_bearing_axes=_promoted_axes(promote),
    )


def naturalness_floor_check(
    review: NaturalnessReview, floors: NaturalnessFloors
) -> list[CheckResult]:
    """Flag each naturalness axis scoring below its floor.

    Mirrors ``rubric_floor_check``: an un-promoted floor stays advisory/log-only;
    ``floors.load_bearing`` promotes a finding into a revision target. Naturalness
    is NOT wired into the deterministic gate in this phase, so with the default
    (un-promoted) floors these findings are advisory.
    """
    results: list[CheckResult] = []
    for axis in K_NATURALNESS_AXES:
        score = getattr(review.scores, axis)
        floor = floors.floor_for(axis)
        if score < floor:
            load_bearing = floors.is_axis_load_bearing(axis)
            results.append(
                CheckResult(
                    check_id="naturalness_floor",
                    unit_id=axis,
                    severity="blocker",
                    advisory=not load_bearing,
                    revision_target=load_bearing,
                    message=(
                        f"naturalness axis {axis!r} scored {score} < floor {floor} "
                        "(golden minimum)"
                    ),
                    fix_hint=f"raise {axis} to at least {floor} (the weakest golden's level)",
                )
            )
    return results


def save_naturalness_floors(
    floors: NaturalnessFloors, *, path: Path | None = None
) -> Path:
    path = Path(path) if path else K_NATURALNESS_FLOORS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(floors.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_naturalness_floors(path: Path | None = None) -> NaturalnessFloors:
    path = Path(path) if path else K_NATURALNESS_FLOORS_PATH
    if not path.exists():
        return NaturalnessFloors()
    return NaturalnessFloors.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_naturalness_regression_seeds(
    path: Path | None = None,
) -> list[NaturalnessRegressionSeed]:
    """Load the idiomatic-marker regression seeds (real bad→good pairs)."""
    path = Path(path) if path else K_NATURALNESS_SEEDS_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [NaturalnessRegressionSeed.model_validate(s) for s in payload.get("seeds", [])]


def _promoted_axes(promote: NaturalnessPromotion) -> list[str]:
    if promote is True or promote is False:
        return []
    if promote == "idiomatic_phrasing":
        return ["idiomatic_phrasing"]
    invalid = [axis for axis in promote if axis not in K_NATURALNESS_AXES]
    if invalid:
        raise ValueError(f"unknown naturalness axis/axes for promotion: {invalid}")
    return sorted(set(promote))


__all__ = [
    "NaturalnessFloors",
    "NaturalnessPromotion",
    "NaturalnessScorer",
    "NaturalnessRegressionSeed",
    "compute_naturalness_floors",
    "naturalness_floor_check",
    "save_naturalness_floors",
    "load_naturalness_floors",
    "load_naturalness_regression_seeds",
    "K_NATURALNESS_FLOORS_PATH",
    "K_NATURALNESS_SEEDS_PATH",
    "K_DEFAULT_EXCLUDE_BELOW",
]
