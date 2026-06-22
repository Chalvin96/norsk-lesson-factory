"""Entry point: ``compute_rubric_floors`` / ``rubric_floor_check`` / ``RubricFloors``.

Corpus-free calibration for the pedagogy judge. Rather than measuring
defect-detection f1 against a seeded-bad corpus (which only works for the shadow
judges — see [[project_calibration_corpus_mismatch]]), this derives a per-axis
quality FLOOR from the reviewer's own scores on the GOLDEN lessons, then gates any
new lesson that scores below a floor on any rubric axis.

Successor to the orphaned ``data/rubric_calibration.overflagging.json`` (phase-1
checkpoint, producer code lost). Same shape: ``axis_floor`` + ``per_golden`` +
``excluded_low`` + ``overall_{min,mean}``.

Pipeline:
1. Run the pedagogy reviewer on each golden -> ``PedagogyReview`` (7 axis scores).
2. Exclude goldens whose overall percent is below ``exclude_below`` (a low-quality
   golden would drag every floor down).
3. Floor for each axis = the minimum score across the KEPT goldens. A new lesson
   scoring below that floor on that axis is flagged.

Promotion to load-bearing stays explicit (``promote=True``), mirroring the f1
thresholds path.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.checks.validators.pedagogy import (
    K_PEDAGOGY_RUBRIC_AXES,
    PedagogyReview,
    pedagogy_percent,
)

K_RUBRIC_FLOORS_RELATIVE_PATH = Path("data") / "calibration" / "rubric_floors.json"
K_RUBRIC_FLOORS_PATH = Path(__file__).resolve().parents[4] / K_RUBRIC_FLOORS_RELATIVE_PATH

# A golden scoring below this overall percent is too weak to define a quality floor.
K_DEFAULT_EXCLUDE_BELOW = 60.0

# Subtracted from each axis's golden minimum: gate quality collapse, not a 1-point dip.
K_DEFAULT_FLOOR_TOLERANCE = 1

# A pedagogy reviewer over a lesson -> a validated review (or None if the backend
# produced nothing). Matches ``judges.pedagogy_review(lesson=...)`` after validation.
RubricScorer = Callable[[dict[str, Any]], PedagogyReview | None]


class RubricFloors(BaseModel):
    """Per-axis quality floors derived from the golden corpus."""

    model: str = "unknown"
    n_goldens: int = 0
    n_in_benchmark: int = 0
    excluded_low: list[str] = Field(default_factory=list)
    overall_min: float = 0.0
    overall_mean: float = 0.0
    axis_floor: dict[str, int] = Field(default_factory=dict)
    per_golden: dict[str, dict[str, Any]] = Field(default_factory=dict)
    load_bearing: bool = False

    def floor_for(self, axis: str) -> int:
        return self.axis_floor.get(axis, 0)


def compute_rubric_floors(
    goldens: dict[str, dict[str, Any]],
    scorer: RubricScorer,
    *,
    model: str = "unknown",
    exclude_below: float = K_DEFAULT_EXCLUDE_BELOW,
    floor_tolerance: int = K_DEFAULT_FLOOR_TOLERANCE,
    promote: bool = False,
) -> RubricFloors:
    """Derive per-axis floors from the reviewer's scores on the golden lessons.

    ``scorer`` is the pedagogy reviewer (lesson -> PedagogyReview | None). Goldens
    that score below ``exclude_below`` overall, or that the scorer cannot review,
    are excluded from the floor computation and listed in ``excluded_low``.

    ``floor_tolerance`` is subtracted from each axis's golden minimum so the gate
    blocks quality *collapse* rather than a single-point dip below the best
    goldens (the per-axis golden min is often 4-5). Default 1. Raises
    ``ValueError`` if no golden survives (no basis for a floor).
    """
    per_golden: dict[str, dict[str, Any]] = {}
    excluded_low: list[str] = []
    kept: dict[str, PedagogyReview] = {}

    for slug in sorted(goldens):
        review = scorer(goldens[slug])
        if review is None:
            excluded_low.append(slug)
            per_golden[slug] = {"overall": None, "excluded": True, "reason": "no_review"}
            continue
        overall = pedagogy_percent(review)
        axis_scores = {axis: getattr(review.scores, axis) for axis in K_PEDAGOGY_RUBRIC_AXES}
        entry = {"overall": overall, "scores": axis_scores}
        if overall < exclude_below:
            excluded_low.append(slug)
            entry["excluded"] = True
            entry["reason"] = "below_exclude_threshold"
        else:
            kept[slug] = review
        per_golden[slug] = entry

    if not kept:
        raise ValueError(
            f"no golden cleared the exclude_below={exclude_below} bar; cannot derive floors "
            f"(reviewed {len(goldens)}, all excluded: {excluded_low})"
        )

    axis_floor = {
        axis: max(0, min(getattr(kept[s].scores, axis) for s in kept) - floor_tolerance)
        for axis in K_PEDAGOGY_RUBRIC_AXES
    }
    overalls = [pedagogy_percent(kept[s]) for s in kept]
    return RubricFloors(
        model=model,
        n_goldens=len(goldens),
        n_in_benchmark=len(kept),
        excluded_low=sorted(excluded_low),
        overall_min=round(min(overalls), 2),
        overall_mean=round(sum(overalls) / len(overalls), 2),
        axis_floor=axis_floor,
        per_golden=per_golden,
        load_bearing=promote,
    )


def rubric_floor_check(review: PedagogyReview, floors: RubricFloors) -> list[CheckResult]:
    """Flag each rubric axis where the lesson scores below its floor.

    ``floors.load_bearing`` promotes a floor finding into a revision target for the
    fix/regenerate loop. It remains non-blocking at final export/human accept; an
    un-promoted floor stays advisory/log-only.
    """
    results: list[CheckResult] = []
    advisory = not floors.load_bearing
    revision_target = floors.load_bearing
    for axis in K_PEDAGOGY_RUBRIC_AXES:
        score = getattr(review.scores, axis)
        floor = floors.floor_for(axis)
        if score < floor:
            results.append(
                CheckResult(
                    check_id="rubric_floor",
                    unit_id=axis,
                    severity="blocker",
                    advisory=advisory,
                    revision_target=revision_target,
                    message=f"pedagogy axis {axis!r} scored {score} < floor {floor} (golden minimum)",
                    fix_hint=f"raise {axis} to at least {floor} (the weakest golden's level)",
                )
            )
    return results


def save_rubric_floors(floors: RubricFloors, *, path: Path | None = None) -> Path:
    path = Path(path) if path else K_RUBRIC_FLOORS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(floors.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_rubric_floors(path: Path | None = None) -> RubricFloors:
    path = Path(path) if path else K_RUBRIC_FLOORS_PATH
    if not path.exists():
        return RubricFloors()
    return RubricFloors.model_validate(json.loads(path.read_text(encoding="utf-8")))


def rubric_floors_path_for_repo(repo_root: Path) -> Path:
    """Resolve the rubric-floors file for a specific repo root."""
    return Path(repo_root) / K_RUBRIC_FLOORS_RELATIVE_PATH


__all__ = [
    "RubricFloors",
    "RubricScorer",
    "compute_rubric_floors",
    "rubric_floor_check",
    "save_rubric_floors",
    "load_rubric_floors",
    "rubric_floors_path_for_repo",
    "K_RUBRIC_FLOORS_PATH",
    "K_DEFAULT_EXCLUDE_BELOW",
]
