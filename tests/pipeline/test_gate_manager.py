"""Tests for the deterministic gate registry (``_DETERMINISTIC_GATE``) and
``gate_lesson_results``: the registry replaces a hand-maintained call sequence,
so these tests pin (a) registry membership + order and (b) real-lesson output,
so a future edit to the registry cannot silently drop/reorder a validator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.checks.gate_manager import (
    _DETERMINISTIC_GATE,
    GateContext,
    gate_lesson_results,
)

K_REPO_ROOT = Path(__file__).resolve().parents[2]
K_EXPECTED_REGISTRY_ORDER = (
    "exercise_structural",
    "objective_structural",
    "objective_coverage",
    "bloom_alignment",
    "nynorsk",
    "requirement_anchors",
    "regression",
    "stale",
    "render_bug",
    "find_fix_integrity",
    "terminology",
    "answer_leak",
    "canned_opener",
    "language_slot",
)


def _load_lesson(slug: str) -> dict[str, Any]:
    path = K_REPO_ROOT / "data" / "lessons" / f"{slug}.json"
    return json.loads(path.read_text())


def test_deterministic_gate_registry_given_no_edits_expect_exact_name_and_order():
    assert tuple(name for name, _ in _DETERMINISTIC_GATE) == K_EXPECTED_REGISTRY_ORDER


def test_deterministic_gate_registry_given_empty_lesson_expect_every_entry_callable():
    # An empty-ish lesson dict exercises the no-op paths of every validator
    # without raising, proving each registry entry is wired to a real callable
    # that accepts a GateContext.
    ctx = GateContext(
        lesson={},
        slug="unknown",
        requirements=None,
        baseline_export=None,
        recorded_requirements_hash=None,
    )
    for name, validator in _DETERMINISTIC_GATE:
        result = validator(ctx)
        assert isinstance(result, list), f"{name} did not return a list"


def test_gate_lesson_results_given_past_tense_lesson_expect_pinned_bloom_alignment_finding():
    lesson = _load_lesson("past_tense")
    results = gate_lesson_results(lesson)
    tuples = [(r.check_id, r.unit_id, r.severity, r.advisory) for r in results]
    assert tuples == [("bloom_alignment", "o1", "warning", False)]


def test_gate_lesson_results_given_possessive_placement_lesson_expect_pinned_trivial_build_finding():
    lesson = _load_lesson("possessive_placement")
    results = gate_lesson_results(lesson)
    tuples = [(r.check_id, r.unit_id, r.severity, r.advisory) for r in results]
    assert tuples == [("trivial_build", "ex_build_neutral_1", "warning", False)]


def test_gate_lesson_results_given_adjective_agreement_lesson_expect_pinned_trivial_build_finding():
    lesson = _load_lesson("adjective_agreement")
    results = gate_lesson_results(lesson)
    tuples = [(r.check_id, r.unit_id, r.severity, r.advisory) for r in results]
    # Pin the deterministic (non-advisory) findings exactly. The canned_opener
    # findings are advisory and their count varies with the corpus repair pass,
    # so assert their shape rather than their exact identity.
    deterministic = [t for t in tuples if not t[3]]
    assert deterministic == [("trivial_build", "ex_build_predicative", "warning", False)]
    advisory = [t for t in tuples if t[3]]
    assert all(cid == "canned_opener" and sev == "warning" for cid, _uid, sev, _adv in advisory)
