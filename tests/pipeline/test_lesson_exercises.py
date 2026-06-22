from lesson_builder.pipeline.lesson_exercises import expected_answers, extract_answer_questions


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


def test_extract_answer_questions_given_mixed_exercises_expect_visible_question_shapes():
    questions = extract_answer_questions(_lesson())
    assert [question["id"] for question in questions] == ["ex_choose", "ex_judge", "ex_recall"]
    assert questions[0]["options"][1]["option_id"] == "b"
    assert questions[2]["sentence"] == "Han ___"


def test_expected_answers_given_mixed_exercises_expect_answer_key_by_id():
    expected = expected_answers(_lesson())
    assert expected["ex_choose"]["answer"] == "b"
    assert expected["ex_judge"]["answer"] is True
    assert expected["ex_recall"]["answer"] == [1]
