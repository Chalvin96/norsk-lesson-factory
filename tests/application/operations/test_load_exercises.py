"""Entry point: tests for the exercises.yaml loader (`load_exercises`).

Covers the 9 ops, the hard cases from design §3.2, non-canonical id preservation,
``[BLANK]`` resolution, ``correct: true`` projection, many-to-one match_pairs,
null-feedback judge, build display-order ≠ answer-order + fixed tokens,
find_fix by token_id with repeated text, and error paths.
"""

from __future__ import annotations

import pytest
import yaml

from lesson_builder.application.operations.load_exercises import load_exercises  # noqa: F401 (used in __all__)

# ── Helpers ──────────────────────────────────────────────────────────────


# ── choose ───────────────────────────────────────────────────────────────


def test_load_exercises_given_choose_with_curved_option_ids_and_correct_true_expect_answer_id_projected():
    exercise = {
        "handle": "ex_choose",
        "op": "choose",
        "objective": "obj1",
        "bloom": "understand",
        "prompt_md": "Choose the form.",
        "options": [
            {"id": "a", "text": "god", "correct": True, "why": "base form"},
            {"id": "b", "text": "godt", "why": "neuter form"},
            {"id": "c", "text": "gode", "why": "plural form"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.operation == "choose"
    assert ex.payload.answer_id == "a"
    ids = [o.option_id for o in ex.payload.options]
    assert ids == ["a", "b", "c"]
    correct = [o for o in ex.payload.options if o.option_id == "a"][0]
    assert correct.text == "god"
    assert correct.why == "base form"


def test_load_exercises_given_choose_with_non_canonical_ids_expect_preserved_verbatim():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "remember",
        "prompt_md": "Pick.",
        "options": [
            {"id": "opt_fordi", "text": "fordi", "correct": True},
            {"id": "da", "text": "da"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.payload.answer_id == "opt_fordi"
    assert [o.option_id for o in ex.payload.options] == ["opt_fordi", "da"]


def test_load_exercises_given_choose_with_blank_in_stem_md_expect_blank_marker_text():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "stem_md": "Jeg leser en [BLANK] bok.",
        "options": [
            {"id": "a", "text": "god", "correct": True},
            {"id": "b", "text": "godt"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.payload.stem is not None
    text_values = [s.value for s in ex.payload.stem if s.kind == "text"]
    combined = "".join(text_values)
    assert "[BLANK]" in combined


def test_load_exercises_given_choose_without_stem_expect_stem_none():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "x", "correct": True},
            {"id": "b", "text": "y"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.payload.stem is None


def test_load_exercises_given_choose_numeric_stem_md_expect_contextual_type_error():
    exercise = {
        "handle": "ex_choose",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "stem_md": 1,
        "options": [
            {"id": "a", "text": "ja", "correct": True},
            {"id": "b", "text": "nei"},
        ],
    }

    with pytest.raises(TypeError, match="ex_choose.*stem_md.*string"):
        _load_one(exercise)


def test_load_exercises_given_choose_no_correct_option_expect_value_error():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "x"},
            {"id": "b", "text": "y"},
        ],
    }
    with pytest.raises(ValueError, match="no option has correct: true"):
        _load_one(exercise)


def test_load_exercises_given_choose_multiple_correct_expect_value_error():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "x", "correct": True},
            {"id": "b", "text": "y", "correct": True},
        ],
    }
    with pytest.raises(ValueError, match="multiple options have correct: true"):
        _load_one(exercise)


def test_load_exercises_given_internal_stage_field_expect_value_error():
    exercise = {
        "handle": "ex_choose",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "stage": "notice",
        "options": [
            {"id": "a", "text": "ja", "correct": True},
            {"id": "b", "text": "nei"},
        ],
    }

    with pytest.raises(ValueError, match="internal field.*stage"):
        _load_one(exercise)


def test_load_exercises_given_choose_duplicate_option_id_expect_value_error():
    exercise = {
        "handle": "ex1",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "same", "text": "first", "correct": True},
            {"id": "same", "text": "second"},
        ],
    }

    with pytest.raises(ValueError, match="duplicate choose option id"):
        _load_one(exercise)


def test_load_exercises_given_duplicate_yaml_key_expect_value_error():
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_exercises(
            "- handle: first\n"
            "  handle: second\n"
            "  op: judge\n"
            "  objective: o\n"
            "  bloom: understand\n"
            "  prompt_md: Judge.\n"
            "  sentence_md: Test.\n"
            "  is_correct: true\n"
        )


def test_load_exercises_given_duplicate_handles_with_distinct_ids_expect_value_error():
    exercises = [
        {
            "handle": "same-handle",
            "id": "first-id",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Judge.",
            "sentence_md": "Jeg går.",
            "is_correct": True,
        },
        {
            "handle": "same-handle",
            "id": "second-id",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Judge again.",
            "sentence_md": "Jeg løper.",
            "is_correct": True,
        },
    ]

    with pytest.raises(ValueError, match="duplicate exercise handle"):
        load_exercises(_dump(exercises))


def test_load_exercises_given_malformed_item_before_duplicate_handle_expect_handle_error_first():
    exercises = [
        {
            "handle": "malformed-early",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "sentence_md": "Jeg går.",
            "is_correct": True,
        },
        {
            "handle": "duplicate-handle",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Judge.",
            "sentence_md": "Jeg går.",
            "is_correct": True,
        },
        {
            "handle": "duplicate-handle",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Judge again.",
            "sentence_md": "Jeg løper.",
            "is_correct": True,
        },
    ]
    with pytest.raises(ValueError, match="duplicate exercise handle"):
        load_exercises(_dump(exercises))


def test_load_exercises_given_blank_handle_expect_value_error():
    with pytest.raises(ValueError, match="handle.*non-empty string"):
        load_exercises(
            _dump(
                [
                    {
                        "handle": " ",
                        "op": "judge",
                        "objective": "o",
                        "bloom": "understand",
                        "prompt_md": "Judge.",
                        "sentence_md": "Jeg går.",
                        "is_correct": True,
                    }
                ]
            )
        )


def test_load_exercises_given_choose_string_correct_flag_expect_type_error():
    exercise = {
        "handle": "ex_choose",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "ja", "correct": "false"},
            {"id": "b", "text": "nei", "correct": True},
        ],
    }

    with pytest.raises(TypeError, match="'correct'.*boolean"):
        _load_one(exercise)


# ── recall_fill ──────────────────────────────────────────────────────────


def test_load_exercises_given_recall_fill_without_audio_target_expect_type_error():
    with pytest.raises(TypeError, match="audio_target"):
        load_exercises(
            _dump(
                [
                    {
                        "handle": "missing-audio-target",
                        "op": "recall_fill",
                        "objective": "o",
                        "bloom": "remember",
                        "prompt_md": "Fill.",
                        "segments": [
                            {"text_md": "Jeg "},
                            {"blank_id": "verb", "options": ["er", "var"], "answer_index": 0},
                        ],
                    }
                ]
            )
        )


def test_load_exercises_given_recall_fill_multi_blank_with_options_expect_all_blanks_preserved():
    exercise = {
        "handle": "ex_recall",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Fill in.",
        "segments": [
            {"text_md": "Vil du ha vann "},
            {"blank_id": "b1", "options": ["eller", "for", "så"], "answer_index": 0},
            {"text_md": " juice? Jeg blir hjemme, "},
            {"blank_id": "b2", "options": ["men", "for", "og"], "answer_index": 1},
        ],
    }
    ex = _load_one(exercise)
    blanks = [s for s in ex.payload.segments if s.kind == "blank"]
    assert len(blanks) == 2
    assert blanks[0].blank_id == "b1"
    assert blanks[0].options == ["eller", "for", "så"]
    assert blanks[0].answer_index == 0
    assert blanks[1].blank_id == "b2"
    assert blanks[1].answer_index == 1


def test_load_exercises_given_recall_fill_with_non_canonical_blank_id_expect_preserved():
    exercise = {
        "handle": "ex1",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Fill.",
        "segments": [
            {"text_md": "et "},
            {"blank_id": "blank_1", "options": ["fin", "fint", "fine"], "answer_index": 1},
            {"text_md": " hus"},
        ],
    }
    ex = _load_one(exercise)
    blank = [s for s in ex.payload.segments if s.kind == "blank"][0]
    assert blank.blank_id == "blank_1"


def test_load_exercises_given_recall_fill_segment_ordering_expect_preserved():
    exercise = {
        "handle": "ex1",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Fill.",
        "segments": [
            {"text_md": "A "},
            {"blank_id": "b1", "options": ["x", "y"], "answer_index": 0},
            {"text_md": " B"},
        ],
    }
    ex = _load_one(exercise)
    kinds = [s.kind for s in ex.payload.segments]
    assert kinds == ["span", "blank", "span"]


def test_load_exercises_given_recall_fill_inline_blank_marker_expect_rejects_source():
    exercise = {
        "handle": "ex_inline_marker",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Fill.",
        "segments": [
            {"text_md": "Amir [BLANK] kommer."},
            {"blank_id": "b1", "options": ["skal", "har"], "answer_index": 0},
        ],
    }

    with pytest.raises(ValueError, match="typed blank segment"):
        _load_one(exercise)


def test_load_exercises_given_recall_fill_alphanumeric_boundary_without_space_expect_value_error():
    exercise = {
        "handle": "ex_boundary",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Complete the sentence.",
        "segments": [
            {"text_md": "Hvis timen ikke"},
            {"blank_id": "verb", "options": ["passer", "passet"], "answer_index": 0},
        ],
    }

    with pytest.raises(ValueError, match="whitespace"):
        _load_one(exercise)


def test_load_exercises_given_recall_fill_quoted_boundary_space_expect_space_preserved():
    exercise = {
        "handle": "ex_quoted_boundary",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Complete the sentence.",
        "segments": [
            {"text_md": "Hvis timen ikke "},
            {"blank_id": "verb", "options": ["passer", "passet"], "answer_index": 0},
        ],
    }

    ex = _load_one(exercise)

    assert ex.payload.segments[0].spans[-1].value == " "


def test_load_exercises_given_recall_duplicate_blank_id_expect_value_error():
    exercise = {
        "handle": "ex_recall",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Fill.",
        "segments": [
            {"text_md": "Han "},
            {"blank_id": "same", "options": ["er", "var"], "answer_index": 0},
            {"text_md": " hjemme. Hun "},
            {"blank_id": "same", "options": ["er", "var"], "answer_index": 1},
        ],
    }

    with pytest.raises(ValueError, match="duplicate recall blank id"):
        _load_one(exercise)


def test_load_exercises_given_recall_duplicate_option_expect_value_error():
    exercise = {
        "handle": "ex_recall",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Fill.",
        "segments": [
            {"text_md": "Han "},
            {"blank_id": "same", "options": ["er", " ER "], "answer_index": 0},
        ],
    }

    with pytest.raises(ValueError, match="duplicate options"):
        _load_one(exercise)


def test_load_exercises_given_recall_fill_punctuation_boundary_expect_loaded_exercise():
    exercise = {
        "handle": "ex_punctuation_boundary",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Complete the sentence.",
        "segments": [
            {"text_md": "Hvis timen ikke passer,"},
            {"blank_id": "result", "options": ["kan", "ville"], "answer_index": 0},
        ],
    }

    ex = _load_one(exercise)

    assert ex.operation == "recall_fill"


# ── match_pairs ──────────────────────────────────────────────────────────


def test_load_exercises_given_match_pairs_many_to_one_expect_pairs_preserved():
    exercise = {
        "handle": "ex_match",
        "op": "match_pairs",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Match.",
        "left": [
            {"left_id": "l1", "text": "Marias leilighet"},
            {"left_id": "l2", "text": "leiligheten til Maria"},
            {"left_id": "l3", "text": "barnets rom"},
            {"left_id": "l4", "text": "rommet til barnet"},
        ],
        "right": [
            {"right_id": "r1", "text": "owner + -s + indefinite"},
            {"right_id": "r2", "text": "definite + til + owner"},
        ],
        "pairs": [
            {"left_id": "l1", "right_id": "r1"},
            {"left_id": "l2", "right_id": "r2"},
            {"left_id": "l3", "right_id": "r1"},
            {"left_id": "l4", "right_id": "r2"},
        ],
    }
    ex = _load_one(exercise)
    assert len(ex.payload.left) == 4
    assert len(ex.payload.right) == 2
    assert len(ex.payload.pairs) == 4
    r1_links = [p.left_id for p in ex.payload.pairs if p.right_id == "r1"]
    assert sorted(r1_links) == ["l1", "l3"]


def test_load_exercises_given_match_pairs_side_ids_expect_preserved_verbatim():
    exercise = {
        "handle": "ex1",
        "op": "match_pairs",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Match.",
        "left": [{"left_id": "lhs_a", "text": "A"}],
        "right": [{"right_id": "rhs_x", "text": "X"}],
        "pairs": [{"left_id": "lhs_a", "right_id": "rhs_x"}],
    }
    ex = _load_one(exercise)
    assert ex.payload.left[0].left_id == "lhs_a"
    assert ex.payload.right[0].right_id == "rhs_x"


def test_load_exercises_given_duplicate_match_pair_expect_value_error():
    exercise = {
        "handle": "ex_match",
        "op": "match_pairs",
        "objective": "o",
        "bloom": "remember",
        "prompt_md": "Match.",
        "left": [{"left_id": "l1", "text": "A"}],
        "right": [{"right_id": "r1", "text": "X"}],
        "pairs": [
            {"left_id": "l1", "right_id": "r1"},
            {"left_id": "l1", "right_id": "r1"},
        ],
    }

    with pytest.raises(ValueError, match="duplicate match pair"):
        _load_one(exercise)


def test_load_exercises_given_duplicate_match_visible_text_expect_value_error():
    exercise = {
        "handle": "ex_match",
        "op": "match_pairs",
        "objective": "o",
        "bloom": "remember",
        "prompt_md": "Match.",
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

    with pytest.raises(ValueError, match="duplicate left item visible text"):
        _load_one(exercise)


# ── judge ────────────────────────────────────────────────────────────────


def test_load_exercises_given_judge_with_feedback_null_expect_none():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Du snakker tydelig.",
        "is_correct": True,
        "feedback": None,
    }
    ex = _load_one(exercise)
    assert ex.payload.is_correct is True
    assert ex.payload.feedback is None


def test_load_exercises_given_judge_with_feedback_absent_expect_none():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Du snakker tydelig.",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    assert ex.payload.feedback is None


def test_load_exercises_given_judge_with_feedback_string_expect_string():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Huset er fin.",
        "is_correct": False,
        "feedback": "Use fint with neuter: Huset er fint.",
    }
    ex = _load_one(exercise)
    assert ex.payload.is_correct is False
    assert ex.payload.feedback == "Use fint with neuter: Huset er fint."


def test_load_exercises_given_judge_with_sentence_spans_expect_parsed():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "[Huset er fint.]{lang=nb}",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    assert len(ex.payload.sentence) >= 1
    kinds = {s.kind for s in ex.payload.sentence}
    assert "foreign_term" in kinds


# ── categorize ───────────────────────────────────────────────────────────


def test_load_exercises_given_categorize_with_explicit_ids_expect_all_preserved():
    exercise = {
        "handle": "ex_cat",
        "op": "categorize",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Sort.",
        "buckets": [
            {"bucket_id": "common_sg", "label": "common-gender singular"},
            {"bucket_id": "neuter_sg", "label": "neuter singular"},
            {"bucket_id": "plural", "label": "plural"},
        ],
        "items": [
            {"item_id": "i1", "text": "en fin bil", "bucket_id": "common_sg"},
            {"item_id": "i2", "text": "et fint hus", "bucket_id": "neuter_sg"},
            {"item_id": "i3", "text": "fine biler", "bucket_id": "plural"},
        ],
    }
    ex = _load_one(exercise)
    assert [b.bucket_id for b in ex.payload.buckets] == ["common_sg", "neuter_sg", "plural"]
    assert [i.item_id for i in ex.payload.items] == ["i1", "i2", "i3"]
    assert ex.payload.items[0].bucket_id == "common_sg"


def test_load_exercises_given_duplicate_category_label_expect_value_error():
    exercise = {
        "handle": "ex_cat",
        "op": "categorize",
        "objective": "o",
        "bloom": "analyze",
        "prompt_md": "Sort.",
        "buckets": [
            {"bucket_id": "a", "label": "Positive"},
            {"bucket_id": "b", "label": " positive "},
        ],
        "items": [{"item_id": "i1", "text": "ja", "bucket_id": "a"}],
    }

    with pytest.raises(ValueError, match="duplicate category bucket visible text"):
        _load_one(exercise)


# ── build ────────────────────────────────────────────────────────────────


def test_load_exercises_given_build_answer_order_differs_from_display_expect_both_preserved():
    exercise = {
        "handle": "ex_build",
        "op": "build",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Build.",
        "tokens": [
            {"token_id": "t1", "text": "den"},
            {"token_id": "t2", "text": "store"},
            {"token_id": "t3", "text": "bilen"},
        ],
        "answer_order": ["t3", "t2", "t1"],
    }
    ex = _load_one(exercise)
    display_ids = [t.token_id for t in ex.payload.tokens]
    assert display_ids == ["t1", "t2", "t3"]
    assert ex.payload.answer_order == ["t3", "t2", "t1"]


def test_load_exercises_given_build_with_fixed_token_expect_fixed_preserved():
    exercise = {
        "handle": "ex_build",
        "op": "build",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Build.",
        "tokens": [
            {"token_id": "t1", "text": "Jeg", "fixed": False},
            {"token_id": "t2", "text": "kom", "fixed": False},
            {"token_id": "t3", "text": ".", "fixed": True},
        ],
        "answer_order": ["t1", "t2", "t3"],
    }
    ex = _load_one(exercise)
    fixed_flags = {t.token_id: t.fixed for t in ex.payload.tokens}
    assert fixed_flags == {"t1": False, "t2": False, "t3": True}


def test_load_exercises_given_build_fixed_field_absent_expect_defaults_to_false():
    exercise = {
        "handle": "ex1",
        "op": "build",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Build.",
        "tokens": [
            {"token_id": "t1", "text": "a"},
            {"token_id": "t2", "text": "b"},
        ],
        "answer_order": ["t1", "t2"],
    }
    ex = _load_one(exercise)
    assert all(not t.fixed for t in ex.payload.tokens)


def test_load_exercises_given_build_string_fixed_flag_expect_type_error():
    exercise = {
        "handle": "ex_build",
        "op": "build",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Build.",
        "tokens": [{"token_id": "t1", "text": "Jeg", "fixed": "false"}],
        "answer_order": ["t1"],
    }

    with pytest.raises(TypeError, match="'fixed'.*boolean"):
        _load_one(exercise)


def test_load_exercises_given_recall_fill_boolean_answer_index_expect_type_error():
    exercise = {
        "handle": "ex_recall",
        "op": "recall_fill",
        "objective": "o",
        "bloom": "remember",
        "prompt_md": "Fill.",
        "segments": [{"blank_id": "verb", "options": ["er", "var"], "answer_index": True}],
    }

    with pytest.raises(TypeError, match="integer answer_index"):
        _load_one(exercise)


# ── find_fix ─────────────────────────────────────────────────────────────


def test_load_exercises_given_find_fix_with_repeated_token_text_expect_targeted_by_id():
    exercise = {
        "handle": "ex_fix",
        "op": "find_fix",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Fix.",
        "tokens": [
            {"token_id": "t1", "text": "Hvis"},
            {"token_id": "t2", "text": "jeg"},
            {"token_id": "t3", "text": "hadde"},
            {"token_id": "t4", "text": "penger,"},
            {"token_id": "t5", "text": "ville"},
            {"token_id": "t6", "text": "jeg"},
            {"token_id": "t7", "text": "reiser"},
            {"token_id": "t8", "text": "til"},
            {"token_id": "t9", "text": "Oslo"},
        ],
        "error_token_id": "t7",
        "feedback": "Use infinitive after ville.",
    }
    ex = _load_one(exercise)
    assert ex.payload.error_token_id == "t7"
    texts = [t.text for t in ex.payload.tokens]
    assert texts.count("jeg") == 2
    error_text = [t.text for t in ex.payload.tokens if t.token_id == "t7"][0]
    assert error_text == "reiser"


def test_load_exercises_given_find_fix_feedback_required_expect_type_error_when_absent():
    exercise = {
        "handle": "ex1",
        "op": "find_fix",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Fix.",
        "tokens": [{"token_id": "t1", "text": "a"}],
        "error_token_id": "t1",
    }
    with pytest.raises(TypeError, match="field 'feedback' must be a string"):
        _load_one(exercise)


# ── speak ────────────────────────────────────────────────────────────────


def test_load_exercises_given_speak_target_expect_exact_target_payload():
    exercise = {
        "handle": "ex_speak",
        "op": "speak",
        "objective": "obj-conditional",
        "bloom": "apply",
        "prompt_md": "Say the sentence that fits the situation.",
        "target": "Hvis timen ikke passer, kan jeg få en annen time.",
    }

    ex = _load_one(exercise)

    assert ex.operation == "speak"
    assert ex.payload.target == "Hvis timen ikke passer, kan jeg få en annen time."


def test_load_exercises_given_speak_whitespace_target_expect_value_error():
    exercise = {
        "handle": "ex_speak",
        "op": "speak",
        "objective": "obj-conditional",
        "bloom": "remember",
        "prompt_md": "Say it.",
        "target": "   ",
    }

    with pytest.raises(ValueError, match="non-whitespace"):
        _load_one(exercise)


def test_load_exercises_given_speak_runtime_field_expect_value_error():
    exercise = {
        "handle": "ex_speak",
        "op": "speak",
        "objective": "obj-conditional",
        "bloom": "apply",
        "prompt_md": "Say it.",
        "target": "Hvis timen passer, kommer jeg.",
        "stt": {"provider": "browser"},
    }

    with pytest.raises(ValueError, match="speak does not support field.*stt"):
        _load_one(exercise)


# ── write ─────────────────────────────────────────────────────────────────


def test_load_exercises_given_write_rubric_expect_llm_judged_payload():
    exercise = {
        "handle": "ex_write",
        "op": "write",
        "objective": "obj-message",
        "bloom": "apply",
        "prompt_md": "Write a short message in Norwegian.",
        "response_language": "no",
        "min_words": 12,
        "max_words": 40,
        "judge_prompt": "Judge only the criteria below; do not invent new requirements.",
        "criteria": [
            {"id": "purpose", "instruction": "The message fulfils its stated purpose."},
            {"id": "clarity", "instruction": "The message is understandable Norwegian."},
        ],
    }

    ex = _load_one(exercise)

    assert ex.operation == "write"
    assert ex.payload.response_language == "no"
    assert ex.payload.min_words == 12
    assert [criterion.id for criterion in ex.payload.criteria] == ["purpose", "clarity"]


def test_load_exercises_given_write_runtime_field_expect_value_error():
    exercise = {
        "handle": "ex_write",
        "op": "write",
        "objective": "obj-message",
        "bloom": "apply",
        "prompt_md": "Write a message.",
        "judge_prompt": "Judge the message.",
        "criteria": [{"id": "purpose", "instruction": "Meet the purpose."}],
        "session_state": {"score": 1},
    }

    with pytest.raises(ValueError, match="write does not support field.*session_state"):
        _load_one(exercise)


# ── Wrapper fields ───────────────────────────────────────────────────────


def test_load_exercises_given_id_field_overrides_handle_expect_curated_id():
    exercise = {
        "handle": "choose-common",
        "id": "ex_choose_common",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "x", "correct": True},
            {"id": "b", "text": "y"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.id == "ex_choose_common"


def test_load_exercises_given_no_id_field_expect_handle_as_id():
    exercise = {
        "handle": "my-handle",
        "op": "choose",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Choose.",
        "options": [
            {"id": "a", "text": "x", "correct": True},
            {"id": "b", "text": "y"},
        ],
    }
    ex = _load_one(exercise)
    assert ex.id == "my-handle"


def test_load_exercises_given_derived_from_entries_expect_preserved():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Test.",
        "is_correct": True,
        "derived_from": [
            {"section_id": "sec-rule", "block_index": 2, "note": "grammar"},
        ],
    }
    ex = _load_one(exercise)
    assert len(ex.derived_from) == 1
    assert ex.derived_from[0].section_id == "sec-rule"
    assert ex.derived_from[0].block_index == 2
    assert ex.derived_from[0].note == "grammar"


def test_load_exercises_given_derived_from_absent_expect_empty_list():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Test.",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    assert ex.derived_from == []


def test_load_exercises_given_derived_from_null_expect_empty_list():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Test.",
        "is_correct": True,
        "derived_from": None,
    }
    ex = _load_one(exercise)
    assert ex.derived_from == []


def test_load_exercises_given_explanation_md_expect_parsed_spans():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "explanation_md": "[Bok]{lex=bok_1} is common-gender.",
        "sentence_md": "Test.",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    assert ex.explanation is not None
    annotated = [s for s in ex.explanation if s.kind == "annotated"]
    assert len(annotated) == 1
    assert annotated[0].metadata == {"lex": "bok_1"}


def test_load_exercises_given_no_explanation_md_expect_none():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "sentence_md": "Test.",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    assert ex.explanation is None


def test_load_exercises_given_numeric_explanation_md_expect_contextual_type_error():
    exercise = {
        "handle": "ex_judge",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Judge.",
        "explanation_md": 1,
        "sentence_md": "Test.",
        "is_correct": True,
    }

    with pytest.raises(TypeError, match="ex_judge.*explanation_md.*string"):
        _load_one(exercise)


def test_load_exercises_given_prompt_md_with_inline_annotation_expect_parsed():
    exercise = {
        "handle": "ex1",
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "Is [bok]{lex=bok_1} correct?",
        "sentence_md": "Test.",
        "is_correct": True,
    }
    ex = _load_one(exercise)
    annotated = [s for s in ex.prompt if s.kind == "annotated"]
    assert len(annotated) == 1
    assert annotated[0].value == "bok"


# ── Multiple exercises in one document ───────────────────────────────────


def test_load_exercises_given_multiple_exercises_expect_order_preserved():
    exercises = [
        {
            "handle": "ex_a",
            "op": "judge",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Judge this sentence.",
            "sentence_md": "Hun kommer i morgen.",
            "is_correct": True,
        },
        {
            "handle": "ex_b",
            "op": "choose",
            "objective": "o",
            "bloom": "understand",
            "prompt_md": "Choose the correct word.",
            "options": [
                {"id": "x", "text": "x", "correct": True},
                {"id": "y", "text": "y"},
            ],
        },
    ]
    result = load_exercises(_dump(exercises))
    assert len(result) == 2
    assert result[0].id == "ex_a"
    assert result[1].id == "ex_b"
    assert result[0].operation == "judge"
    assert result[1].operation == "choose"


# ── Error paths ──────────────────────────────────────────────────────────


def test_load_exercises_given_unknown_op_expect_value_error():
    exercise = {
        "handle": "ex1",
        "op": "bogus",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "x",
    }
    with pytest.raises(ValueError, match="unknown op"):
        _load_one(exercise)


def test_load_exercises_given_missing_handle_expect_value_error():
    exercise = {
        "op": "judge",
        "objective": "o",
        "bloom": "understand",
        "prompt_md": "x",
        "sentence_md": "x",
        "is_correct": True,
    }
    with pytest.raises(ValueError, match="field 'handle'"):
        _load_one(exercise)


def test_load_exercises_given_not_a_list_expect_type_error():
    with pytest.raises(TypeError, match="must be a YAML list"):
        load_exercises("handle: ex1\nop: judge\n")


def test_load_exercises_given_empty_yaml_expect_empty_list():
    assert load_exercises("") == []


def test_load_exercises_given_pydantic_validation_error_expect_value_error():
    # answer_order is not a permutation of token_ids → pydantic validator fails.
    exercise = {
        "handle": "ex1",
        "op": "build",
        "objective": "o",
        "bloom": "apply",
        "prompt_md": "Build.",
        "tokens": [
            {"token_id": "t1", "text": "a"},
            {"token_id": "t2", "text": "b"},
        ],
        "answer_order": ["t1", "t3"],
    }
    with pytest.raises((ValueError, Exception), match="permutation"):
        _load_one(exercise)


def _dump(exercises: list[dict]) -> str:
    """Serialize a list of exercise dicts to YAML text."""
    return yaml.dump(exercises, sort_keys=False, allow_unicode=True)


def _load_one(exercise: dict):
    """Load a single exercise dict and return the resulting Exercise."""
    if exercise.get("op") == "recall_fill":
        exercise = {"audio_target": "Jeg kommer hjem.", **exercise}
    result = load_exercises(_dump([exercise]))
    assert len(result) == 1
    return result[0]
