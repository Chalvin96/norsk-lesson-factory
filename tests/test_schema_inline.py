import pytest
from pydantic import TypeAdapter, ValidationError

from lesson_builder.schema.inline import InlineSpan

_adapter = TypeAdapter(InlineSpan)


def test_text_span_parses():
    span = _adapter.validate_python({"kind": "text", "value": "the house"})
    assert span.value == "the house"


def test_foreign_term_requires_lang():
    span = _adapter.validate_python({"kind": "foreign_term", "value": "huset", "lang": "no"})
    assert span.lang == "no"


def test_foreign_term_lang_must_be_no_or_en():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "foreign_term", "value": "x", "lang": "de"})


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "blink", "value": "x"})


def test_no_markdown_chars_in_value():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "text", "value": "this is **bold**"})
