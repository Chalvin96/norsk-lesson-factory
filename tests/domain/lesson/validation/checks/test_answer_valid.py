from lesson_builder.domain.lesson.validation.checks.validators.answer_valid import answer_valid_check
from lesson_builder.domain.lesson.validation.checks.validators.answer_valid import compare_answers
from lesson_builder.domain.lesson.validation.review_payloads import build_expected_answers


def _lesson() -> dict:
    return {
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_choose",
                "operation": "choose",
                "prompt": [{"kind": "text", "value": "Pick one."}],
                "payload": {
                    "stem": [{"kind": "text", "value": "Choose."}],
                    "options": [{"option_id": "a", "text": "A"}, {"option_id": "b", "text": "B"}],
                    "answer_id": "b",
                },
            },
            {
                "element_kind": "exercise",
                "id": "ex_judge",
                "operation": "judge",
                "prompt": [{"kind": "text", "value": "True or false?"}],
                "payload": {
                    "sentence": [{"kind": "text", "value": "Jeg går hjem."}],
                    "is_correct": True,
                    "feedback": None,
                },
            },
            {
                "element_kind": "exercise",
                "id": "ex_recall",
                "operation": "recall_fill",
                "prompt": [{"kind": "text", "value": "Fill it."}],
                "payload": {
                    "segments": [
                        {"kind": "span", "spans": [{"kind": "text", "value": "Han "}]},
                        {"kind": "blank", "blank_id": "b1", "options": ["er", "var"], "answer_index": 1},
                    ]
                },
            },
        ]
    }


def test_compare_answers_given_judge_string_true_expect_passed_comparison():
    comparison = compare_answers(
        build_expected_answers(_lesson()),
        {
            "answers": [
                {"id": "ex_choose", "answer": "b"},
                {"id": "ex_judge", "answer": "correct"},
                {"id": "ex_recall", "answer": [1]},
            ]
        },
    )
    assert comparison["passed"] is True
    assert comparison["matches"] == 3


def test_answer_valid_check_given_mismatch_and_missing_answers_expect_advisory_blocker():
    results, comparison = answer_valid_check(
        _lesson(),
        {"answers": [{"id": "ex_choose", "answer": "a", "reason": "guessed"}]},
    )
    assert comparison["passed"] is False
    assert comparison["missing"] == ["ex_judge", "ex_recall"]
    assert [m["id"] for m in comparison["mismatches"]] == ["ex_choose"]
    assert len(results) == 1
    assert results[0].check_id == "answer_valid"
    assert results[0].advisory is True
    assert results[0].is_blocking is False


def test_answer_valid_check_given_matching_review_payload_expect_no_results():
    results, comparison = answer_valid_check(
        _lesson(),
        {
            "answers": [
                {"id": "ex_choose", "answer": "b"},
                {"id": "ex_judge", "answer": True},
                {"id": "ex_recall", "answer": [1]},
            ]
        },
    )
    assert results == []
    assert comparison["passed"] is True


def test_answer_valid_check_given_ambiguous_review_in_strict_mode_expect_blocking_result():
    results, comparison = answer_valid_check(
        _lesson(),
        {
            "answers": [
                {"id": "ex_choose", "status": "ambiguous", "answer": None},
                {"id": "ex_judge", "answer": True},
                {"id": "ex_recall", "answer": [1]},
            ]
        },
        strict=True,
    )

    assert comparison["ambiguous"] == ["ex_choose"]
    assert len(results) == 1
    assert results[0].advisory is False
    assert results[0].is_blocking is True


def test_compare_answers_given_unknown_and_duplicate_ids_expect_failed_comparison():
    comparison = compare_answers(
        build_expected_answers(_lesson()),
        {
            "answers": [
                {"id": "ex_choose", "answer": "b"},
                {"id": "ex_choose", "answer": "b"},
                {"id": "not-an-exercise", "answer": "anything"},
                {"id": "ex_judge", "answer": True},
                {"id": "ex_recall", "answer": [1]},
            ]
        },
    )

    assert comparison["duplicates"] == ["ex_choose"]
    assert comparison["extra"] == ["not-an-exercise"]
    assert comparison["passed"] is False
