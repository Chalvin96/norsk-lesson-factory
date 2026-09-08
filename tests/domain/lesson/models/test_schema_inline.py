import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.inline import InlineSpan

_adapter = TypeAdapter(InlineSpan)


def test_text_span_given_valid_value_expect_parsed():
    span = _adapter.validate_python({"kind": "text", "value": "the house"})
    assert span.value == "the house"


def test_foreign_term_given_missing_language_expect_validation_error():
    span = _adapter.validate_python({"kind": "foreign_term", "value": "huset", "lang": "no"})
    assert span.lang == "no"


def test_foreign_term_given_unsupported_language_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "foreign_term", "value": "x", "lang": "de"})


def test_inline_span_given_unknown_kind_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "blink", "value": "x"})


def test_inline_span_given_markdown_characters_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "text", "value": "this is **bold**"})


# ── annotated span (internal-only) ───────────────────────────────────────


def test_annotated_span_parses_given_value_lang_and_metadata_expect_all_fields_preserved():
    span = _adapter.validate_python(
        {"kind": "annotated", "value": "bok", "lang": "no", "metadata": {"lex": "bok_1", "meaning": "book"}}
    )
    assert span.value == "bok"
    assert span.lang == "no"
    assert span.metadata == {"lex": "bok_1", "meaning": "book"}


def test_annotated_span_given_no_lang_expect_lang_none():
    span = _adapter.validate_python({"kind": "annotated", "value": "hello"})
    assert span.lang is None
    assert span.metadata == {}


def test_annotated_span_given_markdown_value_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "annotated", "value": "**bold**"})


def test_annotated_span_given_extra_field_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "annotated", "value": "x", "bogus": 1})


# ── sentence span (internal-only container) ──────────────────────────────


def test_sentence_span_parses_given_children_expect_recurses_into_inline_union():
    span = _adapter.validate_python(
        {
            "kind": "sentence",
            "metadata": {"tense": "past"},
            "children": [
                {"kind": "text", "value": "I går "},
                {"kind": "annotated", "value": "bok", "lang": "no", "metadata": {"lex": "bok_1"}},
            ],
        }
    )
    assert span.metadata == {"tense": "past"}
    assert len(span.children) == 2
    assert span.children[0].kind == "text"
    assert span.children[1].kind == "annotated"


def test_sentence_span_round_trip_given_model_dump_expect_equal_children():
    original = _adapter.validate_python(
        {
            "kind": "sentence",
            "children": [{"kind": "text", "value": "hi"}],
        }
    )
    dumped = original.model_dump(mode="json")
    reparsed = _adapter.validate_python(dumped)
    assert reparsed.kind == "sentence"
    assert reparsed.children[0].kind == "text"
    assert reparsed.children[0].value == "hi"


def test_sentence_span_given_extra_field_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "sentence", "children": [], "bogus": 1})
