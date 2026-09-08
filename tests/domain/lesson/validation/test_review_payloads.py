"""Behavior tests for lesson review validation projections."""

import json

from lesson_builder.domain.lesson.validation.review_payloads import build_expected_answers
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from lesson_builder.domain.lesson.validation.review_payloads import project_answer_review
from lesson_builder.domain.lesson.validation.review_payloads import restore_answer_review_ids


def test_extract_answer_questions_given_mixed_exercises_expect_visible_question_shapes():
    questions = extract_answer_questions(_lesson())
    assert [question["id"] for question in questions] == ["ex_choose", "ex_judge", "ex_recall", "ex_speak"]
    assert questions[0]["options"][1]["option_id"].startswith("review-option-")
    assert questions[2]["sentence"] == "Han ___"
    assert questions[3]["target"] == "Hvis timen ikke passer, kan jeg få en annen time."


def test_project_answer_review_given_answer_signaling_ids_expect_opaque_deterministic_ids_and_reversible_map():
    lesson = _lesson()
    choose = next(item for item in lesson["elements"] if item["id"] == "ex_choose")
    choose["payload"]["options"] = [
        {"option_id": "correct-answer", "text": "A"},
        {"option_id": "incorrect-answer", "text": "B"},
    ]

    first = project_answer_review(lesson)
    second = project_answer_review(lesson)
    first_options = first.questions[0]["options"]

    assert first.questions == second.questions
    assert all(option["option_id"].startswith("review-option-") for option in first_options)
    assert "correct-answer" not in json.dumps(first.questions)
    assert "incorrect-answer" not in json.dumps(first.questions)
    assert first.review_to_authored_option_ids["ex_choose"] == {
        first_options[0]["option_id"]: "correct-answer",
        first_options[1]["option_id"]: "incorrect-answer",
    }


def test_restore_answer_review_ids_given_opaque_choose_answer_expect_authored_answer():
    lesson = _lesson()
    projection = project_answer_review(lesson)
    opaque_id = projection.questions[0]["options"][1]["option_id"]

    restored = restore_answer_review_ids(
        {"answers": [{"id": "ex_choose", "answer": opaque_id}]},
        lesson,
    )

    assert restored == {"answers": [{"id": "ex_choose", "answer": "b"}]}


def test_build_expected_answers_given_mixed_exercises_expect_answer_key_by_id():
    expected = build_expected_answers(_lesson())
    assert expected["ex_choose"]["answer"] == "b"
    assert expected["ex_judge"]["answer"] is True
    assert expected["ex_recall"]["answer"] == [1]
    assert "ex_speak" not in expected


def test_extract_answer_questions_given_mixed_exercises_expect_answer_assignments_hidden():
    questions = extract_answer_questions(_lesson())
    rendered = json.dumps(questions, ensure_ascii=False)

    assert '"answer_id"' not in rendered
    assert '"answer_index"' not in rendered
    assert '"is_correct"' not in rendered
    assert '"answer_order"' not in rendered
    assert '"error_token_id"' not in rendered
    assert '"feedback"' not in rendered
    assert '"pairs"' not in rendered
    assert '"bucket_id"' not in rendered


def test_extract_attempt_questions_given_hidden_context_expect_standalone_surface_only():
    lesson = _lesson()
    lesson["elements"].append(
        {
            "element_kind": "exercise",
            "id": "ex_write",
            "operation": "write",
            "derived_from": [{"section_id": "sec-hidden"}],
            "prompt": [{"kind": "text", "value": "Write a reply."}],
            "payload": {
                "response_language": "no",
                "criteria": [{"id": "greeting", "instruction": "Use a greeting."}],
                "judge_prompt": "hidden rubric",
            },
        }
    )
    rendered = json.dumps(extract_attempt_questions(lesson), ensure_ascii=False)

    assert "sec-hidden" not in rendered
    assert "criteria" not in rendered
    assert "judge_prompt" not in rendered
    assert "target" not in rendered
    assert "hidden rubric" not in rendered


def test_extract_answer_questions_given_referenced_section_expect_keyless_context_slice():
    lesson = _lesson()
    lesson["elements"].insert(
        0,
        {
            "element_kind": "section",
            "id": "sec-dialogue",
            "role": "model",
            "title": "A short reply",
            "blocks": [
                {
                    "kind": "reading",
                    "spans": [{"kind": "text", "value": "Kommer du i morgen?"}],
                    "translation": "Are you coming tomorrow?",
                    "speaker_id": "anna",
                    "speaker_name": "Anna",
                    "dialogue_id": "reply-1",
                },
                {
                    "kind": "examples",
                    "items": [
                        {
                            "no": [{"kind": "text", "value": "Ja, det gjør jeg."}],
                            "en": [{"kind": "text", "value": "Yes, I do."}],
                        }
                    ],
                },
            ],
        },
    )
    choose = next(item for item in lesson["elements"] if item.get("id") == "ex_choose")
    choose["derived_from"] = [{"section_id": "sec-dialogue"}]

    question = next(item for item in extract_answer_questions(lesson) if item["id"] == "ex_choose")

    assert question["lesson_context"] == [
        {
            "section_id": "sec-dialogue",
            "title": "A short reply",
            "role": "model",
            "blocks": [
                {
                    "kind": "reading",
                    "no": "Kommer du i morgen?",
                    "en": "Are you coming tomorrow?",
                    "speaker": "Anna",
                },
                {
                    "kind": "examples",
                    "items": [{"no": "Ja, det gjør jeg.", "en": "Yes, I do."}],
                },
            ],
        }
    ]
    context_text = str(question["lesson_context"])
    assert "answer_id" not in context_text
    assert "correct" not in context_text


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
            {
                "element_kind": "exercise",
                "id": "ex_speak",
                "operation": "speak",
                "prompt": [{"kind": "text", "value": "Say it."}],
                "payload": {"target": "Hvis timen ikke passer, kan jeg få en annen time."},
            },
        ]
    }
