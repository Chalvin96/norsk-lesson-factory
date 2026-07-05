import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.find_fix_integrity import find_fix_integrity_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/reflexive_pronouns.json").read_text())


def _find_fix_exercise(lesson: dict) -> dict:
    return next(
        el
        for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "find_fix"
    )


def test_find_fix_integrity_check_given_clean_real_lesson_expect_no_blockers():
    assert find_fix_integrity_check(_lesson()) == []


def test_find_fix_integrity_check_given_identical_corrected_sentence_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = _find_fix_exercise(lesson)
    tokens = ex["payload"]["tokens"]
    presented = " ".join(t["text"] for t in tokens)
    ex["payload"]["feedback"] = f"Fix it: {presented}."
    results = find_fix_integrity_check(lesson)
    match = [
        r for r in results
        if r.check_id == "find_fix_no_change" and r.unit_id == ex["id"]
    ]
    assert match
    assert match[0].is_blocking
    assert "identical" in match[0].message


def test_find_fix_integrity_check_given_wrong_error_token_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = _find_fix_exercise(lesson)
    # The real change is at t3 (seg -> dere), but we point error_token_id at t1
    ex["payload"]["error_token_id"] = "t1"
    results = find_fix_integrity_check(lesson)
    match = [
        r for r in results
        if r.check_id == "find_fix_wrong_error_token" and r.unit_id == ex["id"]
    ]
    assert match
    assert match[0].is_blocking
    assert "error_token_id" in match[0].message


def test_find_fix_integrity_check_given_no_corrected_sentence_expect_no_results():
    lesson = deepcopy(_lesson())
    ex = _find_fix_exercise(lesson)
    ex["payload"]["feedback"] = "This sentence has an error somewhere."
    assert find_fix_integrity_check(lesson) == []


def test_find_fix_integrity_check_given_correct_error_token_expect_no_results():
    lesson = deepcopy(_lesson())
    ex = _find_fix_exercise(lesson)
    # The reflexive_pronouns find_fix already has a correct error_token_id
    assert ex["payload"]["error_token_id"] == "t3"
    assert find_fix_integrity_check(lesson) == []


def test_find_fix_integrity_check_given_capitalization_only_fix_expect_no_blocker():
    """A capitalization-only correction is a real change and must not trip the
    case/punctuation-sensitive no-change gate (regression for the false-positive
    blocker where lowercasing collapsed presented==corrected)."""
    lesson = deepcopy(_lesson())
    ex = _find_fix_exercise(lesson)
    ex["payload"]["tokens"] = [
        {"token_id": "t1", "text": "jeg"},
        {"token_id": "t2", "text": "heter"},
        {"token_id": "t3", "text": "Per"},
    ]
    ex["payload"]["error_token_id"] = "t1"
    ex["payload"]["feedback"] = "Capitalize the first word: Jeg heter Per."
    assert find_fix_integrity_check(lesson) == []
