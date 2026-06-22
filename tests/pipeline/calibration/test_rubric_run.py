"""Live driver for rubric-floor calibration — offline via an injected scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.calibration.rubric_floors import load_rubric_floors
from lesson_builder.pipeline.calibration.rubric_run import (
    K_RUBRIC_GOLDEN_SLUGS,
    run_rubric_calibration,
)
from lesson_builder.pipeline.checks.validators.pedagogy import (
    K_PEDAGOGY_RUBRIC_AXES,
    PedagogyReview,
)


def _review(score: int) -> PedagogyReview:
    return PedagogyReview.model_validate(
        {
            "scores": dict.fromkeys(K_PEDAGOGY_RUBRIC_AXES, score),
            "summary": "x",
            "passes": [],
            "issues": [],
        }
    )


def _seed_goldens(repo: Path, slugs) -> None:  # type: ignore[no-untyped-def]
    d = repo / "data" / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    for s in slugs:
        (d / f"{s}.json").write_text(json.dumps({"key": s}), encoding="utf-8")


def test_run_writes_floors_file_and_returns_floors(tmp_path):

    _seed_goldens(tmp_path, K_RUBRIC_GOLDEN_SLUGS)

    def scorer(lesson: dict[str, Any]) -> PedagogyReview:
        return _review(4)

    floors = run_rubric_calibration(repo_root=tmp_path, scorer=scorer, floor_tolerance=0)

    assert floors.n_in_benchmark == len(K_RUBRIC_GOLDEN_SLUGS)
    assert all(floors.axis_floor[a] == 4 for a in K_PEDAGOGY_RUBRIC_AXES)
    persisted = load_rubric_floors(tmp_path / "data" / "calibration" / "rubric_floors.json")
    assert persisted.axis_floor == floors.axis_floor


def test_run_promote_flag_marks_load_bearing(tmp_path):
    _seed_goldens(tmp_path, K_RUBRIC_GOLDEN_SLUGS)
    floors = run_rubric_calibration(
        repo_root=tmp_path, scorer=lambda _l: _review(5), promote=True
    )
    assert floors.load_bearing is True


