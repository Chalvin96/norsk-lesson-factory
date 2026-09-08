import json
from copy import deepcopy

from lesson_builder.domain.lesson.validation.checks.validators.structural import exercise_structural_checks
from tests.paths import K_VALID_LESSON_SCHEMA_PATH


def _lesson():
    return json.loads(K_VALID_LESSON_SCHEMA_PATH.read_text())


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


def test_exercise_structural_checks_given_speak_listen_prompt_expect_no_media_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "speak"
    ex["prompt"] = [{"kind": "text", "value": "Listen and repeat the target."}]
    results = exercise_structural_checks(lesson)
    assert [result for result in results if result.check_id == "media_request"] == []


def test_exercise_structural_checks_given_listeners_word_expect_no_media_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["prompt"] = [{"kind": "text", "value": "Choose the right pronoun for two listeners."}]

    results = exercise_structural_checks(lesson)

    assert [result for result in results if result.check_id == "media_request"] == []


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


def test_exercise_structural_checks_given_derived_build_omits_function_word_expect_blocker():
    lesson = deepcopy(_lesson())
    section = next(el for el in lesson["elements"] if el.get("element_kind") == "section")
    section["blocks"] = [
        {
            "kind": "example",
            "no": [
                {"kind": "text", "value": "Jeg kommer på onsdag."},
            ],
            "en": [{"kind": "text", "value": "I am coming on Wednesday."}],
        }
    ]
    ex = _first_exercise(lesson)
    ex["operation"] = "build"
    ex["derived_from"] = [{"section_id": section["id"], "block_index": 0}]
    ex["payload"] = {
        "tokens": [
            {"token_id": "t1", "text": "Jeg", "fixed": False},
            {"token_id": "t2", "text": "kommer", "fixed": False},
            {"token_id": "t3", "text": "onsdag.", "fixed": False},
        ],
        "answer_order": ["t1", "t2", "t3"],
    }

    result = next(r for r in exercise_structural_checks(lesson) if r.check_id == "derived_build_target")

    assert result.is_blocking
    assert "på" in result.message


def test_exercise_structural_checks_given_duplicate_choose_text_expect_payload_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "choose"
    ex["payload"] = {
        "options": [
            {"option_id": "a", "text": "ja"},
            {"option_id": "b", "text": " JA "},
        ],
        "answer_id": "a",
    }

    result = next(
        result for result in exercise_structural_checks(lesson) if result.check_id == "exercise_payload_integrity"
    )

    assert result.is_blocking
    assert "unique" in result.message


def test_exercise_structural_checks_given_inline_recall_marker_expect_payload_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "recall_fill"
    ex["payload"] = {
        "segments": [
            {"kind": "span", "spans": [{"kind": "text", "value": "Jeg [BLANK]"}]},
            {"kind": "blank", "blank_id": "b1", "options": ["er", "var"], "answer_index": 0},
        ]
    }

    results = exercise_structural_checks(lesson)

    assert any("still contains [BLANK]" in result.message for result in results)


def test_exercise_structural_checks_given_build_answer_order_with_unknown_token_expect_payload_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "build"
    ex["payload"] = {
        "tokens": [{"token_id": "t1", "text": "Jeg", "fixed": False}],
        "answer_order": ["missing"],
    }

    results = exercise_structural_checks(lesson)

    assert any("answer_order" in result.message for result in results)


def test_exercise_structural_checks_given_duplicate_match_text_expect_payload_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "match_pairs"
    ex["payload"] = {
        "left": [
            {"left_id": "l1", "text": "ja"},
            {"left_id": "l2", "text": " JA "},
        ],
        "right": [{"right_id": "r1", "text": "positive"}],
        "pairs": [
            {"left_id": "l1", "right_id": "r1"},
            {"left_id": "l2", "right_id": "r1"},
        ],
    }

    results = exercise_structural_checks(lesson)

    assert any(result.check_id == "exercise_payload_integrity" and "text" in result.message for result in results)


def test_exercise_structural_checks_given_duplicate_category_label_expect_payload_blocker():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "categorize"
    ex["payload"] = {
        "buckets": [
            {"bucket_id": "a", "label": "Positive"},
            {"bucket_id": "b", "label": " positive "},
        ],
        "items": [{"item_id": "i1", "text": "ja", "bucket_id": "a"}],
    }

    results = exercise_structural_checks(lesson)

    assert any(result.check_id == "exercise_payload_integrity" and "label" in result.message for result in results)


def test_exercise_structural_checks_given_non_mapping_match_lists_expect_payload_blockers_without_crash():
    lesson = deepcopy(_lesson())
    ex = _first_exercise(lesson)
    ex["operation"] = "match_pairs"
    ex["payload"] = {"left": None, "right": 3, "pairs": {"left_id": "l1"}}

    results = exercise_structural_checks(lesson)

    assert any(result.check_id == "exercise_payload_integrity" for result in results)


def test_exercise_structural_checks_given_all_closed_answers_first_expect_no_position_finding():
    lesson = deepcopy(_lesson())
    lesson["elements"] = []
    for index in range(4):
        lesson["elements"].append(
            {
                "element_kind": "exercise",
                "id": f"choose-{index}",
                "operation": "choose",
                "payload": {
                    "options": [
                        {"option_id": "a", "text": "A"},
                        {"option_id": "b", "text": "B"},
                    ],
                    "answer_id": "a",
                },
            }
        )

    results = exercise_structural_checks(lesson)

    assert not any(result.check_id in {"answer_position_distribution", "trivial_build"} for result in results)
