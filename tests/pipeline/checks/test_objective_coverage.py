import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.objective_coverage import objective_coverage_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def test_objective_coverage_check_given_clean_real_lesson_expect_no_blockers():
    assert objective_coverage_check(_lesson()) == []


def test_objective_coverage_check_given_objective_not_in_any_section_expect_advisory():
    lesson = deepcopy(_lesson())
    orphan_id = "obj_orphan_section"
    lesson["objectives"].append(
        {"id": orphan_id, "statement": "not in any section", "bloom_targets": ["understand"]}
    )
    results = objective_coverage_check(lesson)
    match = [r for r in results if r.unit_id == orphan_id]
    assert match
    # advisory signal, not a hard block (objectives can be legitimately exercise-only)
    assert not match[0].is_blocking
    assert match[0].severity == "warning"
    assert match[0].advisory
    assert "not covered by any section" in match[0].message


def test_objective_coverage_check_given_objective_in_section_expect_no_results():
    lesson = deepcopy(_lesson())
    obj_id = "obj_extra"
    lesson["objectives"].append(
        {"id": obj_id, "statement": "extra", "bloom_targets": ["understand"]}
    )
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["objective_ids"].append(obj_id)
    results = [r for r in objective_coverage_check(lesson) if r.unit_id == obj_id]
    assert results == []


def test_objective_coverage_check_given_future_perfect_expect_known_gap():
    lesson = json.loads((ROOT / "data/lessons/future_perfect.json").read_text())
    results = objective_coverage_check(lesson)
    match = [r for r in results if r.unit_id == "obj_diagnose"]
    assert match
    assert match[0].advisory and not match[0].is_blocking


def test_objective_coverage_check_given_hvis_vs_om_expect_known_gap():
    lesson = json.loads((ROOT / "data/lessons/hvis_vs_om.json").read_text())
    results = objective_coverage_check(lesson)
    match = [r for r in results if r.unit_id == "obj_optional_om"]
    assert match
    assert match[0].advisory and not match[0].is_blocking
