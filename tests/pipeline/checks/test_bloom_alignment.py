import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.bloom_alignment import bloom_alignment_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def _first_exercise(lesson):
    return next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")


def test_bloom_alignment_check_given_clean_real_lesson_expect_only_unreached_target_warnings():
    results = bloom_alignment_check(_lesson())
    assert [r for r in results if r.severity == "blocker"] == []
    assert all(r.check_id == "bloom_alignment" for r in results)


def test_bloom_alignment_check_given_exercise_bloom_outside_objective_targets_expect_warning():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    obj_id = ex["objective_id"]
    obj = next(o for o in lesson["objectives"] if o["id"] == obj_id)
    ex["bloom_level"] = "analyze"
    if "analyze" in obj["bloom_targets"]:
        obj["bloom_targets"] = [t for t in obj["bloom_targets"] if t != "analyze"]
    results = bloom_alignment_check(lesson)
    off_target = [r for r in results if r.unit_id == ex["id"] and "not in objective" in r.message]
    assert off_target
    assert off_target[0].severity == "warning"
    assert off_target[0].fix_hint


def test_bloom_alignment_check_given_objective_with_no_exercises_expect_warning():
    lesson = deepcopy(_lesson())
    lesson["objectives"].append(
        {"id": "obj_orphan_test", "statement": "orphan", "bloom_targets": ["remember"]}
    )
    results = bloom_alignment_check(lesson)
    orphan = [r for r in results if r.unit_id == "obj_orphan_test"]
    # Exactly one warning: "no linked exercises", not also a duplicate "unreached targets"
    # warning for the same objective (regression for the section-2/section-3 overlap).
    assert len(orphan) == 1
    assert "has no linked exercises" in orphan[0].message


def test_bloom_alignment_check_given_objective_never_reaching_target_expect_warning():
    lesson = deepcopy(_lesson())
    obj = lesson["objectives"][0]
    obj["bloom_targets"] = ["remember", "analyze"]
    for ex in lesson["elements"]:
        if ex.get("element_kind") == "exercise" and ex.get("objective_id") == obj["id"]:
            ex["bloom_level"] = "remember"
    results = bloom_alignment_check(lesson)
    unreached = [
        r for r in results
        if r.unit_id == obj["id"] and "unreached" in r.message
    ]
    assert unreached
    assert "analyze" in unreached[0].message


def test_bloom_alignment_check_given_missing_bloom_level_expect_skipped():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    del ex["bloom_level"]
    # Should not crash; the exercise is simply not assessed for bloom alignment.
    results = bloom_alignment_check(lesson)
    assert all("not in objective" not in r.message for r in results if r.unit_id == ex["id"])
