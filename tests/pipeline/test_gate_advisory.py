"""Tests for ``gate_advisory_results``: the advisory fold composition point.

Issue-list findings (pedagogy / objective_alignment / answer) stay ADVISORY. The
promoted rubric-floor layer turns low pedagogy scores into revision targets
without hard-blocking final export on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

K_AXES = ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth")

from lesson_builder.pipeline.checks.gate_manager import gate_advisory_results  # noqa: E402


def _pedagogy_payload(*, severity: str = "P0", score: int = 5) -> dict[str, Any]:
    return {
        "scores": dict.fromkeys(K_AXES, score),
        "summary": "needs work",
        "passes": [],
        "issues": [
            {"severity": severity, "category": "answerable", "title": "x", "evidence": "e", "fix": "f"},
        ],
    }


def _write_floors(tmp_path: Path, *, floor: int, load_bearing: bool) -> Path:
    floors_path = tmp_path / "data" / "calibration" / "rubric_floors.json"
    floors_path.parent.mkdir(parents=True, exist_ok=True)
    floors_path.write_text(
        json.dumps({"axis_floor": dict.fromkeys(K_AXES, floor), "load_bearing": load_bearing}),
        encoding="utf-8",
    )
    return floors_path


def test_gate_advisory_results_given_pedagogy_p0_issue_expect_advisory_nonblocking(tmp_path: Path):
    # no floors file -> only the advisory pedagogy issue folds in
    results = gate_advisory_results(
        {}, pedagogy_review=_pedagogy_payload(severity="P0"), repo_root=tmp_path
    )

    assert len(results) == 1
    result = results[0]
    assert result.check_id == "pedagogy_check"
    assert result.advisory is True
    assert result.severity == "blocker"
    # issue-list findings NEVER block — they are not promotable
    assert result.is_blocking is False


def test_gate_advisory_results_given_none_reviews_expect_empty_list():
    # setup / execute
    results = gate_advisory_results({})

    # assert
    assert results == []


def test_gate_advisory_results_given_promoted_rubric_floor_below_score_expect_revision_target(tmp_path: Path):
    # floors promoted, floor 5 on every axis; the lesson scores 4
    _write_floors(tmp_path, floor=5, load_bearing=True)

    results = gate_advisory_results(
        {}, pedagogy_review=_pedagogy_payload(score=4), repo_root=tmp_path
    )

    rubric = [r for r in results if r.check_id == "rubric_floor"]
    assert len(rubric) == len(K_AXES)  # every axis below floor
    assert all(r.revision_target for r in rubric)
    assert all(not r.is_blocking for r in rubric)
    # the issue-list finding is still present and still advisory
    assert any(r.check_id == "pedagogy_check" and not r.is_blocking for r in results)


def test_gate_advisory_results_given_unpromoted_rubric_floor_expect_advisory(tmp_path: Path):
    _write_floors(tmp_path, floor=5, load_bearing=False)

    results = gate_advisory_results(
        {}, pedagogy_review=_pedagogy_payload(score=4), repo_root=tmp_path
    )

    rubric = [r for r in results if r.check_id == "rubric_floor"]
    assert rubric and all(r.advisory and not r.is_blocking for r in rubric)
    assert all(not r.revision_target for r in rubric)


def test_gate_advisory_results_given_objective_alignment_review_expect_folded_advisory():
    # setup
    alignment_payload = {
        "passed": False,
        "summary": "one mismatch",
        "issues": [
            {
                "objective_id": "obj_1",
                "severity": "P1",
                "message": "objective not covered",
                "evidence": "no exercise targets it",
                "fix": "add an exercise",
            }
        ],
    }

    # execute
    results = gate_advisory_results({}, objective_alignment_review=alignment_payload)

    # assert
    assert len(results) == 1
    result = results[0]
    assert result.check_id == "objective_alignment"
    assert result.advisory is True
    assert result.is_blocking is False
    assert result.unit_id == "obj_1"
