"""Entry points: ``project_answer_review``, ``extract_answer_questions``, ``extract_attempt_questions``, ``extract_open_rubric_questions``, and ``build_expected_answers``.

``extract_answer_questions`` and ``extract_open_rubric_questions`` serve reviewer
operations; ``extract_attempt_questions`` serves standalone attempt review;
``build_expected_answers`` serves deterministic answer checks.

These functions build review payloads from lesson exercise entities for answer
review and answer-key comparison. Context is deliberately learner-visible only;
answer-bearing exercise payload fields are never copied into it.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from lesson_builder.domain.lesson.models.review_payload import AnswerReviewProjection

ExerciseQuestion = dict[str, Any]
ExpectedAnswer = dict[str, Any]

K_EXERCISE_CONTEXT_MAX_BLOCKS = 8
K_EXERCISE_CONTEXT_MAX_ITEMS = 12
K_EXERCISE_CONTEXT_TEXT_LIMIT = 1200


def project_answer_review(lesson: dict[str, Any]) -> AnswerReviewProjection:
    """Project answer-review questions with deterministic opaque option IDs."""
    sections = _sections_by_id(lesson)
    questions: list[ExerciseQuestion] = []
    option_id_map: dict[str, dict[str, str]] = {}
    for exercise in _exercise_elements(lesson):
        question = _question_for_exercise(exercise)
        if question is None:
            continue
        _blind_option_ids(question, option_id_map)
        context = _extract_exercise_context(exercise, sections)
        if context:
            question["lesson_context"] = context
        questions.append(question)
    return AnswerReviewProjection(questions=questions, review_to_authored_option_ids=option_id_map)


def extract_answer_questions(lesson: dict[str, Any]) -> list[ExerciseQuestion]:
    """Return answer-review questions without authored option identifiers."""
    return project_answer_review(lesson).questions


def restore_answer_review_ids(
    reviewer_payload: dict[str, Any],
    lesson: dict[str, Any],
) -> dict[str, Any]:
    """Restore authored choose IDs in reviewer output using the same projection."""
    restored = deepcopy(reviewer_payload)
    option_id_map = project_answer_review(lesson).review_to_authored_option_ids
    answers = restored.get("answers")
    if not isinstance(answers, list):
        return restored
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        exercise_id = answer.get("id")
        review_answer = answer.get("answer")
        if not isinstance(exercise_id, str) or not isinstance(review_answer, str):
            continue
        authored_id = option_id_map.get(exercise_id, {}).get(review_answer)
        if authored_id is not None:
            answer["answer"] = authored_id
    return restored


def extract_attempt_questions(lesson: dict[str, Any]) -> list[ExerciseQuestion]:
    """Project only what a learner can see and submit at attempt time.

    This projection is intentionally separate from ``extract_answer_questions``:
    referenced lesson sections can help an answer reviewer solve a closed item,
    but they are not part of the exercise attempt payload.  It also removes all
    answer assignments, feedback, open-response criteria, and hidden speak
    targets so a standalone reviewer cannot mistake authoring metadata for
    learner context.
    """
    option_id_map: dict[str, dict[str, str]] = {}
    questions: list[ExerciseQuestion] = []
    for exercise in _exercise_elements(lesson):
        question = _attempt_question_for_exercise(exercise)
        if question is None:
            continue
        _blind_option_ids(question, option_id_map)
        questions.append(question)
    return questions


def extract_open_rubric_questions(lesson: dict[str, Any]) -> list[ExerciseQuestion]:
    """Project write tasks for rubric review with their hidden judging contract."""
    questions: list[ExerciseQuestion] = []
    for exercise in _exercise_elements(lesson):
        if exercise.get("operation") != "write":
            continue
        payload = exercise.get("payload", {})
        questions.append(
            {
                "id": exercise.get("id"),
                "operation": "write",
                "prompt": _spans_text(exercise.get("prompt", [])),
                "response_language": payload.get("response_language", "no"),
                "min_words": payload.get("min_words"),
                "max_words": payload.get("max_words"),
                "criteria": payload.get("criteria", []),
                "judge_prompt": payload.get("judge_prompt", ""),
            }
        )
    return questions


def extract_open_semantic_questions(lesson: dict[str, Any]) -> list[ExerciseQuestion]:
    """Project open tasks with the authored evidence claims for semantic review."""
    questions: list[ExerciseQuestion] = []
    for exercise in _exercise_elements(lesson):
        if exercise.get("operation") not in {"speak", "write"}:
            continue
        question = _question_for_exercise(exercise)
        if question is None:
            continue
        question["objective_id"] = exercise.get("objective_id")
        question["bloom"] = exercise.get("bloom")
        questions.append(question)
    return questions


def build_expected_answers(lesson: dict[str, Any]) -> dict[str, ExpectedAnswer]:
    answers_by_exercise_id: dict[str, ExpectedAnswer] = {}
    for exercise in _exercise_elements(lesson):
        if expected := _expected_for_exercise(exercise):
            answers_by_exercise_id[expected["id"]] = expected
    return answers_by_exercise_id


def _extract_exercise_context(exercise: dict[str, Any], sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Project referenced lesson sections into a bounded keyless context slice.

    Only ``derived_from`` section references are followed. This avoids guessing
    context from document position and keeps the answer reviewer blind to all
    exercise payloads and answer assignments.
    """
    references = exercise.get("derived_from")
    if not isinstance(references, list):
        return []
    projected: list[dict[str, Any]] = []
    block_count = 0
    for reference in references[:K_EXERCISE_CONTEXT_MAX_ITEMS]:
        context = _build_reference_context(reference, sections, K_EXERCISE_CONTEXT_MAX_BLOCKS - block_count)
        if context is not None:
            projected.append(context)
            block_count += len(context["blocks"])
        if block_count >= K_EXERCISE_CONTEXT_MAX_BLOCKS:
            break
    return projected


def _build_reference_context(
    reference: object,
    sections: dict[str, dict[str, Any]],
    block_limit: int,
) -> dict[str, Any] | None:
    """Project one section reference within the remaining block budget."""
    if not isinstance(reference, dict):
        return None
    section_id = reference.get("section_id")
    if not isinstance(section_id, str):
        return None
    section = sections.get(section_id)
    if section is None:
        return None
    raw_blocks = section.get("blocks")
    if not isinstance(raw_blocks, list):
        return None
    block_index = reference.get("block_index")
    if isinstance(block_index, int) and not isinstance(block_index, bool):
        raw_blocks = [raw_blocks[block_index]] if 0 <= block_index < len(raw_blocks) else []
    context_blocks = _build_context_blocks(raw_blocks, block_limit)
    if not context_blocks:
        return None
    return {
        "section_id": section_id,
        "title": _clip_text(section.get("title", "")),
        "role": section.get("role", ""),
        "blocks": context_blocks,
    }


def _build_context_blocks(raw_blocks: list[Any], block_limit: int) -> list[dict[str, Any]]:
    """Project valid blocks until the context budget is exhausted."""
    projected: list[dict[str, Any]] = []
    for raw_block in raw_blocks:
        if len(projected) >= block_limit:
            break
        if not isinstance(raw_block, dict):
            continue
        block = _build_context_block(raw_block)
        if block is not None:
            projected.append(block)
    return projected


def _question_for_exercise(exercise: dict[str, Any]) -> ExerciseQuestion | None:
    exercise_id = exercise.get("id")
    operation = exercise.get("operation")
    payload = exercise.get("payload", {})
    question: ExerciseQuestion = {
        "id": exercise_id,
        "operation": operation,
        "prompt": _spans_text(exercise.get("prompt", [])),
    }
    handlers = {
        "choose": _populate_choose_question,
        "judge": _populate_judge_question,
        "find_fix": _populate_find_fix_question,
        "build": _populate_build_question,
        "recall_fill": _populate_recall_question,
        "match_pairs": _populate_match_pairs_question,
        "categorize": _populate_categorize_question,
        "speak": _populate_speak_question,
        "write": _populate_write_question,
    }
    handler = handlers.get(operation) if isinstance(operation, str) else None
    if handler is None:
        return None
    handler(question, payload)
    return question


def _blind_option_ids(question: ExerciseQuestion, option_id_map: dict[str, dict[str, str]]) -> None:
    """Replace choose IDs in a reviewer projection and retain their reverse map."""
    if question.get("operation") != "choose":
        return
    exercise_id = question.get("id")
    options = question.get("options")
    if not isinstance(exercise_id, str) or not isinstance(options, list):
        return
    reverse_map: dict[str, str] = {}
    for index, option in enumerate(options):
        if not isinstance(option, dict) or not isinstance(option.get("option_id"), str):
            continue
        authored_id = option["option_id"]
        review_id = _opaque_option_id(exercise_id, index, authored_id)
        option["option_id"] = review_id
        reverse_map[review_id] = authored_id
    if reverse_map:
        option_id_map[exercise_id] = reverse_map


def _opaque_option_id(exercise_id: str, index: int, authored_id: str) -> str:
    """Return a deterministic identifier that does not expose authored semantics."""
    material = f"{exercise_id}\x00{index}\x00{authored_id}".encode()
    digest = hashlib.sha256(material).hexdigest()[:16]
    return f"review-option-{digest}"


def _populate_choose_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add learner-visible choose fields to a projected question."""
    question["stem"] = _spans_text(payload.get("stem", [])) if payload.get("stem") else None
    question["options"] = [
        {"option_id": option.get("option_id"), "text": option.get("text")} for option in payload.get("options", [])
    ]


def _populate_judge_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add the learner sentence to a projected judge question."""
    question["sentence"] = _spans_text(payload.get("sentence", []))


def _populate_find_fix_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add learner-visible tokens to a projected find_fix question."""
    question["tokens"] = [
        {"token_id": token.get("token_id"), "text": token.get("text")} for token in payload.get("tokens", [])
    ]


def _populate_build_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add learner-visible ordered tokens to a projected build question."""
    question["tokens"] = [
        {"token_id": token.get("token_id"), "text": token.get("text"), "fixed": token.get("fixed")}
        for token in payload.get("tokens", [])
    ]


def _populate_recall_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add the visible sentence and blank choices to a recall question."""
    question["sentence"] = _segments_text(payload.get("segments", []))
    question["blanks"] = [
        {"blank_id": segment.get("blank_id"), "options": segment.get("options", [])}
        for segment in payload.get("segments", [])
        if segment.get("kind") == "blank"
    ]


def _populate_match_pairs_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add matching choices to a projected question."""
    question["left"] = payload.get("left", [])
    question["right"] = payload.get("right", [])


def _populate_categorize_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add category choices without leaking authored bucket assignments."""
    question["buckets"] = payload.get("buckets", [])
    question["items"] = [
        {"item_id": item.get("item_id"), "text": item.get("text")}
        for item in payload.get("items", [])
        if isinstance(item, dict)
    ]


def _populate_speak_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add the recording target to a projected speak question."""
    question["target"] = payload.get("target", "")


def _populate_write_question(question: ExerciseQuestion, payload: dict[str, Any]) -> None:
    """Add writing rubric fields to a projected question."""
    question["response_language"] = payload.get("response_language", "no")
    question["min_words"] = payload.get("min_words")
    question["max_words"] = payload.get("max_words")
    question["criteria"] = payload.get("criteria", [])
    question["judge_prompt"] = payload.get("judge_prompt", "")


def _attempt_question_for_exercise(exercise: dict[str, Any]) -> ExerciseQuestion | None:
    """Build one honest attempt-time question without hidden assessment data."""
    question = _question_for_exercise(exercise)
    if question is None:
        return None
    for hidden_key in ("target", "criteria", "judge_prompt"):
        question.pop(hidden_key, None)
    _sanitize_attempt_collections(question)
    return question


def _sanitize_attempt_collections(question: ExerciseQuestion) -> None:
    """Strip answer assignments from learner-visible collection fields."""
    fields = (
        ("options", "option_id", "text"),
        ("left", "left_id", "text"),
        ("right", "right_id", "text"),
        ("buckets", "bucket_id", "label"),
        ("items", "item_id", "text"),
    )
    for key, id_key, label_key in fields:
        if key in question:
            question[key] = _select_item_fields(question[key], id_key, label_key=label_key)


def _select_item_fields(items: object, id_key: str, *, label_key: str = "text") -> list[dict[str, Any]]:
    """Keep learner-visible item identifiers and labels, never assignments."""
    if not isinstance(items, list):
        return []
    return [{id_key: item.get(id_key), label_key: item.get(label_key)} for item in items if isinstance(item, dict)]


def _sections_by_id(lesson: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index compiled lesson sections without exposing exercise elements."""
    return {
        str(element["id"]): element
        for element in lesson.get("elements", [])
        if isinstance(element, dict) and element.get("element_kind") == "section" and isinstance(element.get("id"), str)
    }


def _build_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only learner-visible fields from one compiled content block."""
    kind = block.get("kind")
    if not isinstance(kind, str):
        return None
    handlers = {
        "reading": _build_reading_context_block,
        "example": _build_example_context_block,
        "examples": _build_examples_context_block,
        "word_list": _build_word_list_context_block,
        "table": _build_table_context_block,
        "list": _build_list_context_block,
        "callout": _build_callout_context_block,
        "paragraph": _build_text_context_block,
        "heading": _build_text_context_block,
        "rule": _build_text_context_block,
    }
    handler = handlers.get(kind)
    return handler(block) if handler is not None else None


def _build_reading_context_block(block: dict[str, Any]) -> dict[str, Any]:
    """Project a learner-visible reading block."""
    result: dict[str, Any] = {
        "kind": "reading",
        "no": _clip_text(_spans_text(block.get("spans", []))),
        "en": _clip_text(block.get("translation", "")),
    }
    speaker_name = block.get("speaker_name")
    if isinstance(speaker_name, str) and speaker_name.strip():
        result["speaker"] = _clip_text(speaker_name)
    return result


def _build_example_context_block(block: dict[str, Any]) -> dict[str, Any]:
    """Project a bilingual example block."""
    return {
        "kind": "example",
        "no": _clip_text(_spans_text(block.get("no", []))),
        "en": _clip_text(_spans_text(block.get("en", []))),
    }


def _build_examples_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project a bounded list of bilingual examples."""
    items = block.get("items")
    if not isinstance(items, list):
        return None
    examples = [
        {
            "no": _clip_text(_spans_text(item.get("no", []))),
            "en": _clip_text(_spans_text(item.get("en", []))),
        }
        for item in items[:K_EXERCISE_CONTEXT_MAX_ITEMS]
        if isinstance(item, dict)
    ]
    return {"kind": "examples", "items": examples} if examples else None


def _build_word_list_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project a bounded word-list block."""
    items = block.get("items")
    if not isinstance(items, list):
        return None
    words = [
        {
            "term": _clip_text(item.get("term", "")),
            "form": _clip_text(item.get("form", "")),
        }
        for item in items[:K_EXERCISE_CONTEXT_MAX_ITEMS]
        if isinstance(item, dict)
    ]
    return {"kind": "word_list", "items": words} if words else None


def _build_table_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project table headers and bounded rows."""
    headers = block.get("headers")
    rows = block.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None
    return {
        "kind": "table",
        "headers": [_clip_text(_spans_text(value)) for value in headers],
        "rows": [
            [_clip_text(_spans_text(value)) for value in row]
            for row in rows[:K_EXERCISE_CONTEXT_MAX_ITEMS]
            if isinstance(row, list)
        ],
    }


def _build_list_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project a bounded learner-visible list."""
    items = block.get("items")
    if not isinstance(items, list):
        return None
    values = [_clip_text(_spans_text(item)) for item in items[:K_EXERCISE_CONTEXT_MAX_ITEMS] if isinstance(item, list)]
    return {"kind": "list", "items": values} if values else None


def _build_callout_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project learner-visible callout children recursively."""
    children = block.get("blocks")
    if not isinstance(children, list):
        return None
    projected_blocks: list[dict[str, Any]] = [
        child
        for child in (_build_context_block(value) for value in children[:K_EXERCISE_CONTEXT_MAX_ITEMS])
        if child is not None
    ]
    return (
        {"kind": "callout", "level": block.get("level", "note"), "blocks": projected_blocks}
        if projected_blocks
        else None
    )


def _build_text_context_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Project paragraph, heading, or rule text."""
    kind = block.get("kind")
    spans = block.get("statement", []) if kind == "rule" else block.get("spans", [])
    text = _clip_text(_spans_text(spans))
    return {"kind": kind, "text": text} if text else None


def _clip_text(value: object) -> str:
    """Bound context prose while preserving a complete learner-visible prefix."""
    text = value if isinstance(value, str) else str(value or "")
    text = " ".join(text.split())
    if len(text) <= K_EXERCISE_CONTEXT_TEXT_LIMIT:
        return text
    return text[: K_EXERCISE_CONTEXT_TEXT_LIMIT - 1].rstrip() + "…"


def _expected_for_exercise(exercise: dict[str, Any]) -> ExpectedAnswer | None:
    exercise_id = exercise.get("id")
    operation = exercise.get("operation")
    payload = exercise.get("payload", {})
    answer_key = (
        {"choose": "answer_id", "judge": "is_correct", "find_fix": "error_token_id"}.get(operation)
        if isinstance(operation, str)
        else None
    )
    if answer_key is not None:
        answer = payload.get(answer_key)
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
        # A speak target is authored context for an external recording/STT
        # runtime, not a deterministic answer key owned by this repository.
        return None
    return {"id": exercise_id, "operation": operation, "answer": answer}


def _spans_text(spans: object) -> str:
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
