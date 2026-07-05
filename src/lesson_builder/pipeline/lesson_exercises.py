"""Entry points: ``extract_answer_questions`` and ``expected_answers``.

These functions project lesson exercise entities into the shapes used by answer
review and answer-key comparison.
"""

from __future__ import annotations

from typing import Any

ExerciseQuestion = dict[str, Any]
ExpectedAnswer = dict[str, Any]


def extract_answer_questions(lesson: dict[str, Any]) -> list[ExerciseQuestion]:
    questions: list[ExerciseQuestion] = []
    for exercise in _exercise_elements(lesson):
        if question := _question_for_exercise(exercise):
            questions.append(question)
    return questions


def expected_answers(lesson: dict[str, Any]) -> dict[str, ExpectedAnswer]:
    answers_by_exercise_id: dict[str, ExpectedAnswer] = {}
    for exercise in _exercise_elements(lesson):
        if expected := _expected_for_exercise(exercise):
            answers_by_exercise_id[expected["id"]] = expected
    return answers_by_exercise_id


def _question_for_exercise(exercise: dict[str, Any]) -> ExerciseQuestion | None:
    exercise_id = exercise.get("id")
    operation = exercise.get("operation")
    payload = exercise.get("payload", {})
    question: ExerciseQuestion = {
        "id": exercise_id,
        "operation": operation,
        "prompt": _spans_text(exercise.get("prompt", [])),
    }

    if operation == "choose":
        question["stem"] = _spans_text(payload.get("stem", [])) if payload.get("stem") else None
        question["options"] = [
            {"option_id": option.get("option_id"), "text": option.get("text")}
            for option in payload.get("options", [])
        ]
        return question
    if operation == "judge":
        question["sentence"] = _spans_text(payload.get("sentence", []))
        return question
    if operation == "find_fix":
        question["tokens"] = [
            {"token_id": token.get("token_id"), "text": token.get("text")}
            for token in payload.get("tokens", [])
        ]
        return question
    if operation == "build":
        question["tokens"] = [
            {"token_id": token.get("token_id"), "text": token.get("text"), "fixed": token.get("fixed")}
            for token in payload.get("tokens", [])
        ]
        return question
    if operation == "recall_fill":
        blanks = []
        for segment in payload.get("segments", []):
            if segment.get("kind") == "blank":
                blanks.append({"blank_id": segment.get("blank_id"), "options": segment.get("options", [])})
        question["sentence"] = _segments_text(payload.get("segments", []))
        question["blanks"] = blanks
        return question
    if operation == "match_pairs":
        question["left"] = payload.get("left", [])
        question["right"] = payload.get("right", [])
        return question
    if operation == "categorize":
        question["buckets"] = payload.get("buckets", [])
        question["items"] = payload.get("items", [])
        return question
    return None


def _expected_for_exercise(exercise: dict[str, Any]) -> ExpectedAnswer | None:
    exercise_id = exercise.get("id")
    operation = exercise.get("operation")
    payload = exercise.get("payload", {})
    if operation == "choose":
        answer = payload.get("answer_id")
    elif operation == "judge":
        answer = payload.get("is_correct")
    elif operation == "find_fix":
        answer = payload.get("error_token_id")
    elif operation == "build":
        answer = payload.get("answer_order", [])
    elif operation == "recall_fill":
        answer = [
            segment.get("answer_index")
            for segment in payload.get("segments", [])
            if isinstance(segment, dict) and segment.get("kind") == "blank"
        ]
    elif operation == "match_pairs":
        answer = {pair.get("left_id"): pair.get("right_id") for pair in payload.get("pairs", [])}
    elif operation == "categorize":
        answer = {item.get("item_id"): item.get("bucket_id") for item in payload.get("items", [])}
    else:
        return None
    return {"id": exercise_id, "operation": operation, "answer": answer}


def _spans_text(spans: Any) -> str:
    if isinstance(spans, str):
        return spans
    if not isinstance(spans, list):
        return ""
    parts: list[str] = []
    for span in spans:
        if isinstance(span, dict):
            parts.append(str(span.get("value", "")))
        else:
            parts.append(str(span))
    return "".join(parts)


def _segments_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.get("kind") == "span":
            parts.append(_spans_text(segment.get("spans", [])))
        elif segment.get("kind") == "blank":
            parts.append("___")
    return "".join(parts)


def _exercise_elements(lesson: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        element
        for element in lesson.get("elements", [])
        if isinstance(element, dict) and element.get("element_kind") == "exercise"
    ]
