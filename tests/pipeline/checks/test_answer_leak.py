import json
from copy import deepcopy
from pathlib import Path

import pytest

from lesson_builder.pipeline.checks.validators.answer_leak import answer_leak_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


@pytest.mark.parametrize(
    "stem",
    [
        "Meaning: velg riktig",
        "As we both know: Han heter Per",
        "As discussed: dette er svaret",
        "As we mentioned: her er svaret",
        "As noted: dette stemmer",
        "Remember that: svaret er nok",
        "Recall that: dette er kjent",
    ],
)
def test_answer_leak_check_flags_each_whitelist_pattern(stem):
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "choose"
    )
    exercise["payload"]["stem"] = [{"kind": "text", "value": stem}]
    results = [r for r in answer_leak_check(lesson) if r.unit_id == exercise["id"]]
    assert results, f"expected a leak finding for stem: {stem!r}"
    assert results[0].advisory


def test_answer_leak_check_given_clean_real_lesson_expect_no_results():
    assert answer_leak_check(_lesson()) == []


def test_answer_leak_check_given_meaning_gloss_in_choose_stem_expect_warning():
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "choose"
    )
    exercise["payload"]["stem"] = [
        {"kind": "text", "value": "Meaning: velg det riktige svaret"}
    ]
    results = answer_leak_check(lesson)
    match = [r for r in results if r.unit_id == exercise["id"]]
    assert match
    assert match[0].check_id == "answer_leak"
    assert match[0].severity == "warning"
    assert match[0].advisory
    assert not match[0].is_blocking


def test_answer_leak_check_given_as_we_both_know_expect_warning():
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "choose"
    )
    exercise["payload"]["stem"] = [
        {"kind": "text", "value": "As we both know: Han heter Per"}
    ]
    results = answer_leak_check(lesson)
    match = [r for r in results if r.unit_id == exercise["id"]]
    assert match
    assert match[0].advisory


def test_answer_leak_check_given_recall_fill_with_meaning_gloss_expect_warning():
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "recall_fill"
    )
    exercise["payload"]["segments"][0]["spans"] = [
        {"kind": "text", "value": "Meaning: fyll inn riktig svar ___"}
    ]
    results = answer_leak_check(lesson)
    match = [r for r in results if r.unit_id == exercise["id"]]
    assert match
    assert match[0].advisory


def test_answer_leak_check_given_normal_stem_expect_no_results():
    lesson = deepcopy(_lesson())
    exercise = next(
        el for el in lesson["elements"]
        if el.get("element_kind") == "exercise" and el.get("operation") == "choose"
    )
    exercise["payload"]["stem"] = [
        {"kind": "text", "value": "Choose the correct Norwegian sentence."}
    ]
    assert answer_leak_check(lesson) == []
