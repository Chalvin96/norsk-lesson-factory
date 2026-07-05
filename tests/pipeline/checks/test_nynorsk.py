import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.nynorsk import nynorsk_check, nynorsk_scan

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def test_nynorsk_scan_given_marker_text_expect_detected_markers():
    assert nynorsk_scan("Eg gjekk heim, ikkje sant") == ["eg", "gjekk", "ikkje"]


def test_nynorsk_scan_given_shared_bokmal_words_expect_no_markers():
    assert nynorsk_scan("Jeg er i Oslo og går hjem nå.") == []


def test_nynorsk_check_given_clean_lesson_expect_no_results():
    assert nynorsk_check(_lesson()) == []


def test_nynorsk_check_given_answer_content_marker_expect_blocker():
    lesson = deepcopy(_lesson())
    exercise = next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")
    exercise["operation"] = "choose"
    exercise["payload"] = {
        "options": [
            {"option_id": "a", "text": "Dette er riktig"},
            {"option_id": "b", "text": "Dette er ikkje riktig"},
        ],
        "answer_id": "b",
    }
    results = nynorsk_check(lesson)
    assert len(results) == 1
    assert results[0].check_id == "nynorsk_scan"
    assert results[0].unit_id == exercise["id"]
    assert results[0].is_blocking
    assert "ikkje" in results[0].message


def test_nynorsk_check_given_marker_only_in_wrong_choose_option_expect_no_results():
    lesson = deepcopy(_lesson())
    exercise = next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")
    exercise["operation"] = "choose"
    exercise["payload"] = {
        "options": [
            {"option_id": "a", "text": "Dette er riktig"},
            {"option_id": "b", "text": "Dette er ikkje riktig"},
        ],
        "answer_id": "a",
    }

    assert nynorsk_check(lesson) == []


def test_nynorsk_check_given_marker_in_choose_stem_expect_blocker():
    lesson = deepcopy(_lesson())
    exercise = next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")
    exercise["operation"] = "choose"
    exercise["payload"] = {
        "stem": [{"kind": "text", "value": "Kva er riktig Bokmål?"}],
        "options": [
            {"option_id": "a", "text": "Dette er riktig"},
            {"option_id": "b", "text": "Dette er galt"},
        ],
        "answer_id": "a",
    }

    results = nynorsk_check(lesson)

    assert len(results) == 1
    assert "kva" in results[0].message


def test_nynorsk_check_given_false_judge_sentence_expect_skipped_marker():
    lesson = deepcopy(_lesson())
    exercise = next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")
    exercise["operation"] = "judge"
    exercise["payload"] = {
        "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
        "is_correct": False,
        "feedback": "Bokmål would use gikk.",
    }
    assert nynorsk_check(lesson) == []
