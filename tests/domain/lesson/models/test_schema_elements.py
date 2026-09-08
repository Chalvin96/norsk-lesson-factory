import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.elements import Element

_element = TypeAdapter(Element)


def test_section_parses_with_blocks():
    sec = _element.validate_python(
        {
            "element_kind": "section",
            "id": "s1",
            "role": "model",
            "objective_ids": ["o1"],
            "title": "Tutor Explanation",
            "blocks": [{"kind": "paragraph", "spans": [_txt("hi")]}],
        }
    )
    assert sec.element_kind == "section"
    assert sec.role == "model"


def test_recall_fill_exercise_parses():
    ex = _element.validate_python(
        {
            "element_kind": "exercise",
            "id": "e1",
            "operation": "recall_fill",
            "objective_id": "o1",
            "bloom_level": "apply",
            "prompt": [_txt("Fill in")],
            "explanation": None,
            "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
            "payload": {
                "audio_target": "Huset er stort.",
                "segments": [
                    {"kind": "span", "spans": [{"kind": "foreign_term", "value": "Huset er", "lang": "no"}]},
                    {"kind": "blank", "blank_id": "b1", "options": ["stor", "stort"], "answer_index": 1},
                ],
            },
        }
    )
    assert ex.element_kind == "exercise"
    assert ex.operation == "recall_fill"


def test_recall_fill_answer_index_in_range():
    with pytest.raises(ValidationError, match="answer_index"):
        _element.validate_python(
            {
                "element_kind": "exercise",
                "id": "e2",
                "operation": "recall_fill",
                "objective_id": "o1",
                "bloom_level": "remember",
                "prompt": [_txt("x")],
                "explanation": None,
                "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
                "payload": {
                    "audio_target": "Huset er stort.",
                    "segments": [{"kind": "blank", "blank_id": "b1", "options": ["a", "b"], "answer_index": 5}],
                },
            }
        )


def test_judge_payload_parses():
    ex = _element.validate_python(
        {
            "element_kind": "exercise",
            "id": "e3",
            "operation": "judge",
            "objective_id": "o1",
            "bloom_level": "understand",
            "prompt": [_txt("True or false?")],
            "explanation": None,
            "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
            "payload": {
                "sentence": [{"kind": "foreign_term", "value": "Huset er stor", "lang": "no"}],
                "is_correct": False,
                "feedback": "Huset er stort",
            },
        }
    )
    assert ex.payload.is_correct is False


def test_unknown_operation_rejected():
    with pytest.raises(ValidationError):
        _element.validate_python(
            {
                "element_kind": "exercise",
                "id": "e4",
                "operation": "sing_song",
                "objective_id": "o1",
                "bloom_level": "remember",
                "prompt": [_txt("x")],
                "explanation": None,
                "derived_from": [],
                "payload": {},
            }
        )


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        (
            "match_pairs",
            {
                "left": [{"left_id": "l1", "text": "en"}],
                "right": [{"right_id": "r1", "text": "et"}],
                "pairs": [{"left_id": "l1", "right_id": "r1"}],
            },
        ),
        (
            "choose",
            {
                "options": [
                    {"option_id": "a", "text": "stor"},
                    {"option_id": "b", "text": "stort"},
                ],
                "answer_id": "b",
            },
        ),
        (
            "categorize",
            {
                "buckets": [
                    {"bucket_id": "m", "label": "masc"},
                    {"bucket_id": "n", "label": "neut"},
                ],
                "items": [{"item_id": "i1", "text": "hus", "bucket_id": "n"}],
            },
        ),
        (
            "build",
            {
                "tokens": [
                    {"token_id": "t1", "text": "Huset", "fixed": True},
                    {"token_id": "t2", "text": "er", "fixed": False},
                    {"token_id": "t3", "text": "stort", "fixed": False},
                ],
                "answer_order": ["t1", "t2", "t3"],
            },
        ),
        (
            "find_fix",
            {
                "tokens": [
                    {"token_id": "t1", "text": "Huset"},
                    {"token_id": "t2", "text": "er"},
                    {"token_id": "t3", "text": "stor"},
                ],
                "error_token_id": "t3",
                "feedback": "stort",
            },
        ),
        (
            "speak",
            {"target": "Hvis timen ikke passer, kan jeg få en annen time."},
        ),
        (
            "write",
            {
                "response_language": "no",
                "min_words": 12,
                "max_words": 40,
                "judge_prompt": "Judge only the authored criteria and return the app result protocol.",
                "criteria": [
                    {"id": "purpose", "instruction": "The message fulfils its purpose."},
                    {"id": "form", "instruction": "The requested Norwegian form is understandable."},
                ],
            },
        ),
    ],
)
def test_remaining_operations_parse(operation: str, payload: dict):
    ex = _element.validate_python(_exercise(operation, payload, ex_id=f"e_{operation}"))
    assert ex.element_kind == "exercise"
    assert ex.operation == operation


@pytest.mark.parametrize("bad_pair", [{"left_id": "nope", "right_id": "r1"}, {"left_id": "l1", "right_id": "nope"}])
def test_match_pairs_rejects_dangling_pair_reference(bad_pair: dict):
    payload = {
        "left": [{"left_id": "l1", "text": "en"}],
        "right": [{"right_id": "r1", "text": "et"}],
        "pairs": [bad_pair],
    }
    with pytest.raises(ValidationError, match="unknown (left_id|right_id)"):
        _element.validate_python(_exercise("match_pairs", payload, ex_id="e_mp"))


def test_write_payload_given_reversed_word_bounds_expect_rejection():
    with pytest.raises(ValidationError, match="min_words"):
        _element.validate_python(
            _exercise(
                "write",
                {
                    "min_words": 40,
                    "max_words": 12,
                    "judge_prompt": "Judge the response.",
                    "criteria": [{"id": "purpose", "instruction": "Meet the purpose."}],
                },
                ex_id="e_write",
            )
        )


def test_build_payload_given_duplicate_token_id_expect_rejection():
    with pytest.raises(ValidationError, match="duplicate token id"):
        _element.validate_python(
            _exercise(
                "build",
                {
                    "tokens": [
                        {"token_id": "t1", "text": "Huset", "fixed": True},
                        {"token_id": "t1", "text": "er", "fixed": False},
                    ],
                    "answer_order": ["t1", "t1"],
                },
                ex_id="e_build_duplicate",
            )
        )


def test_match_pairs_given_delimiter_like_ids_expect_distinct_pairings():
    ex = _element.validate_python(
        _exercise(
            "match_pairs",
            {
                "left": [
                    {"left_id": "a", "text": "A"},
                    {"left_id": "a->b", "text": "A to B"},
                ],
                "right": [
                    {"right_id": "b->c", "text": "B to C"},
                    {"right_id": "c", "text": "C"},
                ],
                "pairs": [
                    {"left_id": "a", "right_id": "b->c"},
                    {"left_id": "a->b", "right_id": "c"},
                ],
            },
            ex_id="e_match_delimiter_ids",
        )
    )

    assert [(pair.left_id, pair.right_id) for pair in ex.payload.pairs] == [
        ("a", "b->c"),
        ("a->b", "c"),
    ]


def _txt(v: str) -> dict:
    return {"kind": "text", "value": v}


def _exercise(operation: str, payload: dict, *, ex_id: str = "ex") -> dict:
    return {
        "element_kind": "exercise",
        "id": ex_id,
        "operation": operation,
        "objective_id": "o1",
        "bloom_level": "remember",
        "prompt": [_txt("x")],
        "explanation": None,
        "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
        "payload": payload,
    }
