import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.render_bug import render_bug_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def test_render_bug_check_given_clean_real_lesson_expect_no_blockers():
    assert render_bug_check(_lesson()) == []


def test_render_bug_check_given_foreign_term_lang_en_expect_advisory():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"][0]["spans"].append(
        {"kind": "foreign_term", "value": "hello", "lang": "en"}
    )
    results = render_bug_check(lesson)
    match = [r for r in results if r.check_id == "render_bug_lang" and r.unit_id == section["id"]]
    assert match
    # lang="en" is legitimate for inline English contrast terms -> advisory, not blocking
    assert not match[0].is_blocking
    assert match[0].severity == "warning"
    assert match[0].advisory
    assert "lang='en'" in match[0].message


def test_render_bug_check_given_english_function_word_tagged_no_expect_blocker():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"][0]["spans"].append(
        {"kind": "foreign_term", "value": "the", "lang": "no"}
    )
    results = render_bug_check(lesson)
    match = [
        r for r in results
        if r.check_id == "render_bug_english_func_word" and r.unit_id == section["id"]
    ]
    assert match
    assert match[0].is_blocking
    assert "English function word" in match[0].message


def test_render_bug_check_given_norwegian_foreign_term_expect_no_results():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"][0]["spans"].append(
        {"kind": "foreign_term", "value": "bokmål", "lang": "no"}
    )
    assert render_bug_check(lesson) == []


def test_render_bug_check_recurses_into_exercise_payload():
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "judge"
    )
    exercise["payload"]["sentence"].append(
        {"kind": "foreign_term", "value": "the", "lang": "no"}
    )
    results = render_bug_check(lesson)
    match = [r for r in results if r.unit_id == exercise["id"]]
    assert match
    # english-function-word-tagged-no is the unambiguous defect -> blocker, and
    # this confirms the walker recurses into exercise payloads
    assert match[0].is_blocking
    assert match[0].check_id == "render_bug_english_func_word"
