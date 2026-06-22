"""Rubric-floor calibration (#2): derive per-axis quality floors from the golden
corpus and gate lessons that fall below them.

Entry points: ``compute_rubric_floors``, ``rubric_floor_check``, save/load.
"""

from __future__ import annotations

from typing import Any

import pytest

from lesson_builder.pipeline.calibration.rubric_floors import (
    RubricFloors,
    compute_rubric_floors,
    load_rubric_floors,
    rubric_floor_check,
    save_rubric_floors,
)
from lesson_builder.pipeline.checks.validators.pedagogy import (
    K_PEDAGOGY_RUBRIC_AXES,
    PedagogyReview,
)


def _review(score: int = 5, **overrides: int) -> PedagogyReview:
    scores = dict.fromkeys(K_PEDAGOGY_RUBRIC_AXES, score)
    scores.update(overrides)
    return PedagogyReview.model_validate(
        {"scores": scores, "summary": "x", "passes": [], "issues": []}
    )


def _scorer_from(table: dict[str, PedagogyReview | None]):
    def scorer(lesson: dict[str, Any]) -> PedagogyReview | None:
        return table[lesson["key"]]

    return scorer


def _lessons(*slugs: str) -> dict[str, dict[str, Any]]:
    return {s: {"key": s} for s in slugs}


def test_floor_is_min_across_kept_goldens():
    # arrange: three strong goldens, varying per axis.
    table = {
        "a": _review(5, depth=4),
        "b": _review(5, depth=5, complete=3),
        "c": _review(5, complete=4),
    }
    floors = compute_rubric_floors(_lessons("a", "b", "c"), _scorer_from(table), floor_tolerance=0)

    # floor = per-axis minimum across all kept goldens (tolerance 0)
    assert floors.axis_floor["depth"] == 4  # min(4,5,5)
    assert floors.axis_floor["complete"] == 3  # min(5,3,4)
    assert floors.axis_floor["on_concept"] == 5
    assert floors.n_in_benchmark == 3
    assert floors.excluded_low == []


def test_floor_tolerance_lowers_floor_below_golden_min():
    table = {"a": _review(5), "b": _review(5, depth=4)}
    floors = compute_rubric_floors(_lessons("a", "b"), _scorer_from(table), floor_tolerance=1)

    assert floors.axis_floor["depth"] == 3  # min(5,4)=4, minus tolerance 1
    assert floors.axis_floor["on_concept"] == 4  # 5 minus 1
    # never goes negative
    low = compute_rubric_floors(_lessons("a"), _scorer_from({"a": _review(5, depth=0)}), floor_tolerance=2)
    assert low.axis_floor["depth"] == 0


def test_weak_golden_is_excluded_and_does_not_lower_floor():
    # 'weak' scores all 1s -> ~20% overall, below the 60 exclude bar.
    table = {"good1": _review(5), "good2": _review(5), "weak": _review(1)}
    floors = compute_rubric_floors(
        _lessons("good1", "good2", "weak"), _scorer_from(table), floor_tolerance=0
    )

    assert "weak" in floors.excluded_low
    assert floors.n_in_benchmark == 2
    # the weak golden's 1s did NOT drag the floor down
    assert floors.axis_floor["on_concept"] == 5


def test_unreviewable_golden_excluded():
    table = {"ok": _review(5), "dead": None}
    floors = compute_rubric_floors(_lessons("ok", "dead"), _scorer_from(table))

    assert "dead" in floors.excluded_low
    assert floors.per_golden["dead"]["reason"] == "no_review"
    assert floors.n_in_benchmark == 1


def test_all_excluded_raises():
    table = {"weak1": _review(1), "weak2": _review(2)}
    with pytest.raises(ValueError, match="no golden cleared"):
        compute_rubric_floors(_lessons("weak1", "weak2"), _scorer_from(table))


def test_check_flags_axis_below_floor():
    floors = RubricFloors(axis_floor=dict.fromkeys(K_PEDAGOGY_RUBRIC_AXES, 4))
    below = _review(5, depth=2, bokmal=3)

    results = rubric_floor_check(below, floors)

    flagged = {r.unit_id for r in results}
    assert flagged == {"depth", "bokmal"}
    assert all(r.check_id == "rubric_floor" for r in results)


def test_check_passes_when_at_or_above_floor():
    floors = RubricFloors(axis_floor=dict.fromkeys(K_PEDAGOGY_RUBRIC_AXES, 4))
    assert rubric_floor_check(_review(4), floors) == []


def test_rubric_floor_check_given_floor_promotion_expect_revision_target_not_export_blocker():
    axes = dict.fromkeys(K_PEDAGOGY_RUBRIC_AXES, 5)
    low = _review(1)
    advisory = rubric_floor_check(low, RubricFloors(axis_floor=axes, load_bearing=False))
    promoted = rubric_floor_check(low, RubricFloors(axis_floor=axes, load_bearing=True))

    assert advisory and all(r.advisory for r in advisory)
    assert all(not r.revision_target for r in advisory)
    assert promoted and all(not r.advisory for r in promoted)
    assert all(r.revision_target for r in promoted)
    assert all(not r.is_blocking for r in promoted)


def test_save_load_round_trip(tmp_path):
    floors = compute_rubric_floors(
        _lessons("a", "b"), _scorer_from({"a": _review(5), "b": _review(5, depth=4)}),
        model="codex/gpt-5.5", promote=True,
    )
    path = save_rubric_floors(floors, path=tmp_path / "rubric_floors.json")
    loaded = load_rubric_floors(path)

    assert loaded.axis_floor == floors.axis_floor
    assert loaded.model == "codex/gpt-5.5"
    assert loaded.load_bearing is True


def test_load_missing_returns_empty_defaults(tmp_path):
    floors = load_rubric_floors(tmp_path / "nope.json")
    assert floors.n_goldens == 0
    assert floors.load_bearing is False
