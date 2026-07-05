import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.objective_structural import objective_structural_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def test_objective_structural_check_given_clean_real_lesson_expect_no_blockers():
    assert objective_structural_check(_lesson()) == []


def test_objective_structural_check_given_exercise_unknown_objective_id_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")
    ex["objective_id"] = "obj_does_not_exist"
    results = objective_structural_check(lesson)
    match = [r for r in results if r.check_id == "objective_structural" and r.unit_id == ex["id"]]
    assert match
    assert match[0].is_blocking
    assert "unknown objective_id" in match[0].message


def test_objective_structural_check_given_section_unknown_objective_id_expect_blocker():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["objective_ids"] = ["obj_does_not_exist"]
    results = objective_structural_check(lesson)
    match = [r for r in results if r.check_id == "objective_structural" and r.unit_id == section["id"]]
    assert match
    assert match[0].is_blocking


def test_objective_structural_check_given_review_pool_unknown_objective_expect_blocker():
    lesson = deepcopy(_lesson())
    lesson["review_pool"]["pools"].append(
        {"key": "obj_phantom", "objective_id": "obj_phantom", "cards": []}
    )
    results = objective_structural_check(lesson)
    match = [r for r in results if "review_pool" in r.message]
    assert match
    assert match[0].is_blocking


def test_objective_structural_check_given_declared_unused_objective_expect_blocker():
    lesson = deepcopy(_lesson())
    lesson["objectives"].append(
        {"id": "obj_orphan_unused", "statement": "unused", "bloom_targets": ["remember"]}
    )
    results = objective_structural_check(lesson)
    match = [r for r in results if r.unit_id == "obj_orphan_unused"]
    assert match
    assert match[0].is_blocking
    assert "declared but unused" in match[0].message


def test_objective_structural_check_given_objective_only_in_pool_expect_unused_blocker():
    # An objective referenced only by a review_pool entry (no exercises/sections) is
    # still declared-but-unused: the pool is metadata, not pedagogical use.
    lesson = deepcopy(_lesson())
    obj_id = "obj_pool_only"
    lesson["objectives"].append(
        {"id": obj_id, "statement": "pool only", "bloom_targets": ["remember"]}
    )
    lesson["review_pool"]["pools"].append(
        {"key": obj_id, "objective_id": obj_id, "cards": []}
    )
    results = objective_structural_check(lesson)
    match = [r for r in results if r.unit_id == obj_id]
    assert match
    assert match[0].is_blocking
    assert "declared but unused" in match[0].message


def test_objective_structural_check_given_placeholder_slug_statement_expect_blocker():
    # A raw "Learn <slug>" placeholder (anchored, slug-shape only) must be flagged.
    lesson = deepcopy(_lesson())
    lesson["objectives"][0]["statement"] = "Learn foo"
    results = objective_structural_check(lesson)
    match = [r for r in results if r.check_id == "objective_placeholder"]
    assert match
    assert match[0].is_blocking
    assert match[0].unit_id == lesson["objectives"][0]["id"]
    assert "placeholder" in match[0].message


def test_objective_structural_check_given_real_learn_sentence_expect_no_placeholder_blocker():
    # A real objective sentence that starts with "Learn" but has spaces/extra words
    # must NOT match the slug-shape placeholder regex.
    lesson = deepcopy(_lesson())
    lesson["objectives"][0]["statement"] = "Learn common compounds as whole words"
    results = objective_structural_check(lesson)
    placeholder_hits = [r for r in results if r.check_id == "objective_placeholder"]
    assert placeholder_hits == []
