import json
from copy import deepcopy
from pathlib import Path

from lesson_builder.pipeline.checks.validators.structural import exercise_structural_checks

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def _first_exercise(lesson):
    return next(el for el in lesson["elements"] if el.get("element_kind") == "exercise")


def test_exercise_structural_checks_given_clean_lesson_expect_no_blockers():
    results = exercise_structural_checks(_lesson())
    assert [r for r in results if r.is_blocking] == []


def test_exercise_structural_checks_given_false_judge_without_feedback_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "judge"
    ex["payload"] = {"sentence": [{"kind": "text", "value": "x"}], "is_correct": False, "feedback": ""}
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "judge_feedback")
    assert result.is_blocking
    assert result.unit_id == ex["id"]


def test_exercise_structural_checks_given_recall_fill_without_blank_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "recall_fill"
    ex["payload"] = {"segments": [{"kind": "span", "spans": [{"kind": "text", "value": "no blank"}]}]}
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "recall_fill_blanks")
    assert result.is_blocking


def test_exercise_structural_checks_given_audio_prompt_expect_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Listen to the audio and choose."}]
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "media_request")
    assert result.is_blocking


def test_exercise_structural_checks_given_empty_example_block_expect_blocker():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"].append({"kind": "example", "no": [], "en": []})
    result = next(r for r in exercise_structural_checks(lesson) if r.message == "example block with empty no/en")
    assert result.is_blocking
    assert result.unit_id == section["id"]


def test_exercise_structural_checks_given_header_only_table_expect_blocker():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"] = [{"kind": "table", "headers": [[{"kind": "text", "value": "A"}]], "rows": []}]
    result = next(r for r in exercise_structural_checks(lesson) if r.message == "table with empty body rows")
    assert result.is_blocking


def test_exercise_structural_checks_given_thin_repetitive_lesson_expect_warnings():
    lesson = deepcopy(_lesson())
    lesson["elements"] = [el for el in lesson["elements"] if el.get("element_kind") != "exercise"]
    warnings = [r.message for r in exercise_structural_checks(lesson) if r.severity == "warning"]
    assert "thin_lesson" in warnings
    assert "repetitive_operation_mix" in warnings


def test_exercise_structural_checks_given_trivial_build_expect_warning():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "build"
    ex["payload"] = {
        "tokens": [{"token_id": "t1", "text": "Jeg", "fixed": False}, {"token_id": "t2", "text": "går", "fixed": False}],
        "answer_order": ["t1", "t2"],
    }
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "trivial_build")
    assert result.severity == "warning"


def test_exercise_structural_checks_given_unquoted_gloss_prompt_expect_warning():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Which sentence means We always eat breakfast?"}]
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "unquoted_gloss")
    assert not result.is_blocking
    assert result.unit_id == ex["id"]


def test_exercise_structural_checks_given_quoted_gloss_prompt_expect_no_finding():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Which sentence means “We always eat breakfast”?"}]
    assert [r for r in exercise_structural_checks(lesson) if r.check_id == "unquoted_gloss"] == []


def test_exercise_structural_checks_given_descriptive_means_that_clause_expect_no_finding():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Which sentence means that a solution exists?"}]
    assert [r for r in exercise_structural_checks(lesson) if r.check_id == "unquoted_gloss"] == []


def test_exercise_structural_checks_given_generic_meaning_noun_expect_no_finding():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Match each Norwegian adverb to its meaning."}]
    assert [r for r in exercise_structural_checks(lesson) if r.check_id == "unquoted_gloss"] == []


def test_exercise_structural_checks_given_unquoted_gloss_in_stem_expect_warning():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Choose."}]
    ex.setdefault("payload", {})["stem"] = [
        {"kind": "text", "value": "Which phrase means the small house?"}
    ]
    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "unquoted_gloss")
    assert not result.is_blocking
