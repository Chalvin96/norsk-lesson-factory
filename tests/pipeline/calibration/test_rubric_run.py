"""Live driver for rubric-floor calibration — offline via an injected scorer."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from lesson_builder.pipeline.calibration import rubric_run
from lesson_builder.pipeline.calibration.rubric_floors import load_rubric_floors
from lesson_builder.pipeline.calibration.rubric_run import (
    K_RUBRIC_GOLDEN_SLUGS,
    _assert_goldens_pass_gate,
    run_rubric_calibration,
)
from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.checks.validators.pedagogy import (
    K_PEDAGOGY_RUBRIC_AXES,
    PedagogyReview,
)

K_REPO_ROOT = Path(__file__).resolve().parents[3]


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
    """Copy the REAL golden lessons into ``repo``. They are gate-clean, so the
    golden-hygiene precondition (``_assert_goldens_pass_gate``) passes. Coupling
    the calibration tests to real goldens is intentional: if a golden regresses a
    blocker, both the live calibration path and these tests fail loudly."""
    d = repo / "data" / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    for s in slugs:
        shutil.copyfile(
            K_REPO_ROOT / "data" / "lessons" / f"{s}.json", d / f"{s}.json"
        )


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


# ---------------------------------------------------------------------------
# Golden-hygiene precondition (Phase 9)
# ---------------------------------------------------------------------------


def test_assert_goldens_pass_gate_clean_goldens_does_not_raise():
    # arrange: the real goldens are gate-clean
    goldens = {
        s: json.loads((K_REPO_ROOT / "data" / "lessons" / f"{s}.json").read_text(encoding="utf-8"))
        for s in K_RUBRIC_GOLDEN_SLUGS
    }
    # act / assert: no raise
    _assert_goldens_pass_gate(goldens)


def test_assert_goldens_pass_gate_raises_on_blocking(monkeypatch):
    # arrange: force the gate to report a blocker for one golden
    def fake_gate(lesson, requirements=None):  # noqa: ARG001
        return [
            CheckResult(
                check_id="objective_placeholder",
                severity="blocker",
                unit_id="o1",
                message="placeholder objective",
            )
        ]

    monkeypatch.setattr(rubric_run, "gate_lesson_results", fake_gate)

    # act / assert
    with pytest.raises(ValueError, match="golden-hygiene precondition"):
        _assert_goldens_pass_gate({"collocations": {"key": "collocations"}})


def test_run_rubric_calibration_enforces_golden_gate(tmp_path, monkeypatch):
    # arrange: real goldens seeded, but the gate is forced to block
    _seed_goldens(tmp_path, K_RUBRIC_GOLDEN_SLUGS)

    def fake_gate(lesson, requirements=None):  # noqa: ARG001
        return [
            CheckResult(
                check_id="objective_placeholder",
                severity="blocker",
                unit_id="o1",
                message="placeholder objective",
            )
        ]

    monkeypatch.setattr(rubric_run, "gate_lesson_results", fake_gate)

    # act / assert: calibration refuses before computing floors
    with pytest.raises(ValueError, match="golden-hygiene precondition"):
        run_rubric_calibration(repo_root=tmp_path, scorer=lambda _l: _review(4))


