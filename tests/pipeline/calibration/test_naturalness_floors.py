"""Entry point: ``compute_naturalness_floors`` / ``naturalness_floor_check``.

Naturalness floor derivation + idiomatic-marker regression seeds (Phase 8).
"""

from __future__ import annotations

import pytest

from lesson_builder.pipeline.calibration.naturalness_floors import (
    NaturalnessFloors,
    NaturalnessRegressionSeed,
    compute_naturalness_floors,
    load_naturalness_floors,
    load_naturalness_regression_seeds,
    naturalness_floor_check,
    save_naturalness_floors,
)
from lesson_builder.pipeline.checks.validators.naturalness import (
    K_NATURALNESS_AXES,
    NaturalnessReview,
)


def _review(score: int) -> NaturalnessReview:
    return NaturalnessReview.model_validate(
        {"scores": dict.fromkeys(K_NATURALNESS_AXES, score), "issues": []}
    )


# ---------------------------------------------------------------------------
# compute_naturalness_floors
# ---------------------------------------------------------------------------


def test_compute_naturalness_floors_given_uniform_goldens_expect_floor_minus_tolerance():
    # arrange: three goldens all scoring 5 on every axis
    goldens = {"a": {}, "b": {}, "c": {}}

    # act
    floors = compute_naturalness_floors(goldens, lambda _l: _review(5), floor_tolerance=1)

    # assert: floor = golden-min(5) - tolerance(1) = 4
    assert floors.n_in_benchmark == 3
    assert all(floors.axis_floor[a] == 4 for a in K_NATURALNESS_AXES)


def _scorer(lesson):  # noqa: ANN001
    """Score from the golden dict itself: {"s": <int>|None}. None = backend miss."""
    score = lesson.get("s")
    return _review(score) if score is not None else None


def test_compute_naturalness_floors_given_low_golden_expect_excluded():
    # arrange: one weak golden (score 1 -> 20%) below the 60% exclude bar
    goldens = {"good": {"s": 5}, "weak": {"s": 1}}

    # act
    floors = compute_naturalness_floors(goldens, _scorer, floor_tolerance=0)

    # assert: weak golden excluded, floor set by the surviving golden
    assert "weak" in floors.excluded_low
    assert floors.n_in_benchmark == 1
    assert all(floors.axis_floor[a] == 5 for a in K_NATURALNESS_AXES)


def test_compute_naturalness_floors_given_no_golden_survives_expect_value_error():
    # arrange: every golden scores below the exclude bar
    with pytest.raises(ValueError, match="no golden cleared"):
        compute_naturalness_floors({"a": {"s": 1}}, _scorer)


def test_compute_naturalness_floors_given_unreviewable_golden_expect_excluded():
    # arrange: scorer returns None (backend miss) for one golden
    goldens = {"ok": {"s": 5}, "miss": {"s": None}}
    floors = compute_naturalness_floors(goldens, _scorer, floor_tolerance=0)
    assert "miss" in floors.excluded_low
    assert floors.n_in_benchmark == 1


def test_compute_naturalness_floors_given_idiomatic_promotion_expect_axis_scoped_gate():
    # setup
    goldens = {"good": {"s": 5}}

    # execute
    floors = compute_naturalness_floors(
        goldens,
        _scorer,
        floor_tolerance=0,
        promote="idiomatic_phrasing",
    )

    # assert
    assert floors.load_bearing is False
    assert floors.load_bearing_axes == ["idiomatic_phrasing"]
    assert floors.is_axis_load_bearing("idiomatic_phrasing") is True
    assert floors.is_axis_load_bearing("register_appropriateness") is False


def test_compute_naturalness_floors_given_full_promotion_expect_all_axes_load_bearing():
    # setup
    goldens = {"good": {"s": 5}}

    # execute
    floors = compute_naturalness_floors(goldens, _scorer, floor_tolerance=0, promote=True)

    # assert
    assert floors.load_bearing is True
    assert floors.load_bearing_axes == []
    assert all(floors.is_axis_load_bearing(axis) is True for axis in K_NATURALNESS_AXES)


def test_compute_naturalness_floors_given_unknown_promoted_axis_expect_value_error():
    # setup
    goldens = {"good": {"s": 5}}

    # execute / assert
    with pytest.raises(ValueError, match="unknown naturalness axis/axes"):
        compute_naturalness_floors(
            goldens,
            _scorer,
            floor_tolerance=0,
            promote=("idiomatic_phrasing", "made_up_axis"),
        )


def test_naturalness_floors_round_trip_given_axis_scoped_promotion_expect_preserved(tmp_path):
    # setup
    floors = NaturalnessFloors(
        axis_floor=dict.fromkeys(K_NATURALNESS_AXES, 3),
        load_bearing_axes=["idiomatic_phrasing"],
    )
    path = tmp_path / "naturalness_floors.json"

    # execute
    save_naturalness_floors(floors, path=path)
    loaded = load_naturalness_floors(path)

    # assert
    assert loaded.load_bearing is False
    assert loaded.load_bearing_axes == ["idiomatic_phrasing"]
    assert loaded.is_axis_load_bearing("idiomatic_phrasing") is True
    assert loaded.is_axis_load_bearing("register_appropriateness") is False


def test_load_naturalness_floors_given_legacy_payload_expect_empty_load_bearing_axes(tmp_path):
    # setup
    path = tmp_path / "naturalness_floors.json"
    path.write_text(
        '{"axis_floor": {"idiomatic_phrasing": 3}, "load_bearing": false}\n',
        encoding="utf-8",
    )

    # execute
    floors = load_naturalness_floors(path)

    # assert
    assert floors.load_bearing is False
    assert floors.load_bearing_axes == []
    assert floors.is_axis_load_bearing("idiomatic_phrasing") is False


# ---------------------------------------------------------------------------
# naturalness_floor_check
# ---------------------------------------------------------------------------


def test_naturalness_floor_check_given_unpromoted_floors_expect_advisory_finding():
    # arrange: floor 4, review scores 2 on idiomatic_phrasing
    floors = NaturalnessFloors(axis_floor=dict.fromkeys(K_NATURALNESS_AXES, 4))
    review = NaturalnessReview.model_validate(
        {
            "scores": {
                "idiomatic_phrasing": 2,
                "register_appropriateness": 5,
                "terminology_consistency": 5,
            },
            "issues": [],
        }
    )

    # act
    results = naturalness_floor_check(review, floors)

    # assert: one finding on idiomatic_phrasing, advisory (floors un-promoted)
    assert len(results) == 1
    assert results[0].check_id == "naturalness_floor"
    assert results[0].unit_id == "idiomatic_phrasing"
    assert results[0].advisory is True
    assert not results[0].is_blocking


def test_naturalness_floor_check_given_idiomatic_axis_promoted_expect_only_idiomatic_revision_target():
    # setup
    floors = NaturalnessFloors(
        axis_floor=dict.fromkeys(K_NATURALNESS_AXES, 4),
        load_bearing_axes=["idiomatic_phrasing"],
    )
    review = NaturalnessReview.model_validate(
        {
            "scores": {
                "idiomatic_phrasing": 2,
                "register_appropriateness": 2,
                "terminology_consistency": 2,
            },
            "issues": [],
        }
    )

    # execute
    results = naturalness_floor_check(review, floors)

    # assert
    by_axis = {result.unit_id: result for result in results}
    assert by_axis["idiomatic_phrasing"].advisory is False
    assert by_axis["idiomatic_phrasing"].revision_target is True
    assert by_axis["register_appropriateness"].advisory is True
    assert by_axis["register_appropriateness"].revision_target is False
    assert by_axis["terminology_consistency"].advisory is True
    assert by_axis["terminology_consistency"].revision_target is False


def test_naturalness_floor_check_given_full_promotion_expect_all_findings_revision_targets():
    # setup
    floors = NaturalnessFloors(
        axis_floor=dict.fromkeys(K_NATURALNESS_AXES, 4),
        load_bearing=True,
    )
    review = NaturalnessReview.model_validate(
        {
            "scores": {
                "idiomatic_phrasing": 2,
                "register_appropriateness": 2,
                "terminology_consistency": 2,
            },
            "issues": [],
        }
    )

    # execute
    results = naturalness_floor_check(review, floors)

    # assert
    assert len(results) == 3
    assert all(result.advisory is False for result in results)
    assert all(result.revision_target is True for result in results)


def test_naturalness_floor_check_given_at_or_above_floor_expect_no_findings():
    # setup
    floors = NaturalnessFloors(axis_floor=dict.fromkeys(K_NATURALNESS_AXES, 4))

    # execute / assert
    assert naturalness_floor_check(_review(4), floors) == []


# ---------------------------------------------------------------------------
# regression seeds
# ---------------------------------------------------------------------------


def test_regression_seeds_given_seed_file_expect_well_formed_entries():
    seeds = load_naturalness_regression_seeds()
    # at least the markers caught this session
    assert len(seeds) >= 5
    for seed in seeds:
        assert seed.bad and seed.good
        assert seed.bad != seed.good
        assert seed.intended_meaning
        assert seed.marker
        assert seed.source_commit
    markers = {s.marker for s in seeds}
    assert "reflexive_missing_selv" in markers
    assert "time_without_klokka" in markers


def test_regression_seeds_given_idiomatic_marker_expect_good_form_contains_fix():
    # the corrections add the idiomatic marker the bad form omits
    by_marker = {}
    for seed in load_naturalness_regression_seeds():
        by_marker.setdefault(seed.marker, []).append(seed)
    for seed in by_marker["reflexive_missing_selv"]:
        assert "selv" in seed.good and "selv" not in seed.bad
    for seed in by_marker["time_without_klokka"]:
        assert "klokka" in seed.good and "klokka" not in seed.bad


def test_regression_seeds_given_context_sensitive_pairs_expect_intended_meaning():
    by_bad = {seed.bad: seed for seed in load_naturalness_regression_seeds()}

    assert by_bad["Han kjenner seg."].intended_meaning == "He knows himself."
    assert by_bad["Han kjenner seg."].promotion_candidate is True
    assert by_bad["Vi vet at butikken stenger seks."].intended_meaning.endswith(
        "six o'clock."
    )
    assert by_bad["Vi vet at butikken stenger seks."].promotion_candidate is False


def test_naturalness_regression_seed_given_missing_policy_metadata_expect_validation_error():
    with pytest.raises(ValueError, match="intended_meaning"):
        NaturalnessRegressionSeed.model_validate(
            {
                "marker": "reflexive_missing_selv",
                "slug": "reflexive_pronouns",
                "bad": "Han kjenner seg.",
                "good": "Han kjenner seg selv.",
                "promotion_candidate": True,
                "source_commit": "abc123",
            }
        )

    with pytest.raises(ValueError, match="promotion_candidate"):
        NaturalnessRegressionSeed.model_validate(
            {
                "marker": "reflexive_missing_selv",
                "slug": "reflexive_pronouns",
                "bad": "Han kjenner seg.",
                "good": "Han kjenner seg selv.",
                "intended_meaning": "He knows himself.",
                "source_commit": "abc123",
            }
        )
