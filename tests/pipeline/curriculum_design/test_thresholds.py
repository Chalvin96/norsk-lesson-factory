"""Phase-0 curriculum thresholds: derive thinness / too-broad / coverage floors
from a SMALL fixture set of fake concept cards (NOT the real 104), deterministically.

Entry points: ``compute_curriculum_thresholds``, ``save_curriculum_thresholds``,
``load_curriculum_thresholds``, ``run_curriculum_thresholds``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.pipeline.concept_requirements import ConceptRequirements, Objective
from lesson_builder.pipeline.curriculum_design.thresholds import (
    compute_curriculum_thresholds,
    load_curriculum_thresholds,
    save_curriculum_thresholds,
)
from lesson_builder.pipeline.curriculum_design.thresholds_run import run_curriculum_thresholds


def _card(
    slug: str,
    *,
    n_objectives: int,
    anchors: list[str],
    cefr_level: str = "A1",
) -> ConceptRequirements:
    return ConceptRequirements(
        slug=slug,
        cefr_level=cefr_level,
        objectives=[
            Objective(id=f"o{i}", statement=f"Objective {i}", bloom_targets=["understand"])
            for i in range(n_objectives)
        ],
        required_anchor_forms=anchors,
        notes="fixture card",
    )


def _seed_cards_dir(repo: Path, cards: dict[str, ConceptRequirements]) -> None:
    cards_dir = repo / "data" / "concept_requirements"
    cards_dir.mkdir(parents=True, exist_ok=True)
    for slug, card in cards.items():
        (cards_dir / f"{slug}.json").write_text(
            json.dumps(card.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def test_compute_curriculum_thresholds_given_varied_cards_expect_min_max_floors():
    # setup: three cards with 2, 4, 6 objectives and 3, 5, 2 anchors.
    cards = {
        "thin": _card("thin", n_objectives=2, anchors=["a", "b", "c"]),
        "mid": _card("mid", n_objectives=4, anchors=["a", "b", "c", "d", "e"]),
        "broad": _card("broad", n_objectives=6, anchors=["a", "b"]),
    }

    thresholds = compute_curriculum_thresholds(cards)

    # assert: thinness_floor = min objectives (2), too_broad_ceiling = max (6).
    assert thresholds.thinness_floor == 2
    assert thresholds.too_broad_ceiling == 6
    assert thresholds.coverage_floor.min_objectives == 2
    assert thresholds.coverage_floor.min_anchors == 2
    assert thresholds.n_concepts == 3
    assert thresholds.n_in_benchmark == 3
    assert thresholds.excluded == []
    # median of [2, 4, 6] = 4.0; median of [2, 3, 5] = 3.0
    assert thresholds.objective_count_distribution.median == 4.0
    assert thresholds.anchor_count_distribution.median == 3.0


def test_compute_curriculum_thresholds_given_zero_anchor_card_expect_excluded():
    # setup: one degenerate card (0 anchors) and two healthy cards.
    cards = {
        "degenerate": _card("degenerate", n_objectives=3, anchors=[]),
        "healthy_a": _card("healthy_a", n_objectives=4, anchors=["a", "b"]),
        "healthy_b": _card("healthy_b", n_objectives=5, anchors=["a", "b", "c"]),
    }

    thresholds = compute_curriculum_thresholds(cards)

    # assert: degenerate excluded; floor derived from healthy cards only.
    assert "degenerate" in thresholds.excluded
    assert thresholds.n_in_benchmark == 2
    assert thresholds.thinness_floor == 4  # min(4, 5), NOT 3 from the degenerate card
    assert thresholds.coverage_floor.min_anchors == 2  # min(2, 3)
    # per_concept still records the excluded card's counts
    assert thresholds.per_concept["degenerate"] == {"objectives": 3, "anchors": 0}


def test_compute_curriculum_thresholds_given_all_degenerate_expect_value_error():
    cards = {
        "empty_anchors": _card("empty_anchors", n_objectives=2, anchors=[]),
        "also_empty": _card("also_empty", n_objectives=3, anchors=[]),
    }

    with pytest.raises(ValueError, match="no concept cleared the exclude-low guard"):
        compute_curriculum_thresholds(cards)


def test_compute_curriculum_thresholds_given_promote_flag_expect_load_bearing():
    cards = {"ok": _card("ok", n_objectives=3, anchors=["a"])}

    advisory = compute_curriculum_thresholds(cards, promote=False)
    promoted = compute_curriculum_thresholds(cards, promote=True)

    assert advisory.load_bearing is False
    assert promoted.load_bearing is True


def test_save_load_round_trip(tmp_path):
    cards = {
        "a": _card("a", n_objectives=2, anchors=["x", "y"]),
        "b": _card("b", n_objectives=4, anchors=["x", "y", "z"], cefr_level="B1"),
    }
    thresholds = compute_curriculum_thresholds(cards, promote=True)

    path = save_curriculum_thresholds(thresholds, path=tmp_path / "curriculum_thresholds.json")
    loaded = load_curriculum_thresholds(path)

    assert loaded.thinness_floor == thresholds.thinness_floor
    assert loaded.too_broad_ceiling == thresholds.too_broad_ceiling
    assert loaded.coverage_floor == thresholds.coverage_floor
    assert loaded.objective_count_distribution == thresholds.objective_count_distribution
    assert loaded.load_bearing is True
    assert loaded.per_concept == thresholds.per_concept


def test_load_missing_returns_empty_defaults(tmp_path):
    loaded = load_curriculum_thresholds(tmp_path / "nope.json")

    assert loaded.n_concepts == 0
    assert loaded.thinness_floor == 0
    assert loaded.load_bearing is False


def test_run_curriculum_thresholds_given_seeded_repo_expect_persisted_file(tmp_path):
    # setup: seed a small fake cards dir (NOT the real 104).
    cards = {
        "concept_a": _card("concept_a", n_objectives=3, anchors=["a", "b"]),
        "concept_b": _card("concept_b", n_objectives=5, anchors=["a", "b", "c", "d"]),
    }
    _seed_cards_dir(tmp_path, cards)

    # execute
    thresholds = run_curriculum_thresholds(repo_root=tmp_path)

    # assert
    assert thresholds.n_in_benchmark == 2
    assert thresholds.thinness_floor == 3
    assert thresholds.too_broad_ceiling == 5
    persisted_path = tmp_path / "data" / "calibration" / "curriculum_thresholds.json"
    assert persisted_path.exists()
    persisted = load_curriculum_thresholds(persisted_path)
    assert persisted.thinness_floor == 3
