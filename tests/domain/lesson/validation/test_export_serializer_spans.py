"""Internal-schema span projection: annotated + sentence spans stay off the wire.

These tests verify that:
- The two new internal-only span kinds (``annotated``, ``sentence``) parse and
  round-trip internally.
- The export projection strips them to wire kinds (``foreign_term``/``text``)
  with ``metadata`` dropped and the current packet schema value.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.domain.lesson.models.inline import InlineSpan
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict

_inline_adapter = TypeAdapter(InlineSpan)

_WIRE_KINDS = frozenset({"text", "emphasis", "strong", "code", "foreign_term"})
_INTERNAL_KINDS = frozenset({"annotated", "sentence"})


# ---------------------------------------------------------------------------
# Internal round-trip
# ---------------------------------------------------------------------------


def test_annotated_span_round_trip_given_model_dump_expect_equal_dict():
    original = {
        "kind": "annotated",
        "value": "bok",
        "lang": "no",
        "metadata": {"lex": "bok_1", "meaning": "book"},
    }
    parsed = _inline_adapter.validate_python(original)
    dumped = parsed.model_dump(mode="json")
    assert dumped == original


def test_sentence_span_round_trip_given_model_dump_expect_equal_dict():
    original = {
        "kind": "sentence",
        "metadata": {"tense": "past"},
        "children": [
            {"kind": "text", "value": "I går "},
            {"kind": "annotated", "value": "bok", "lang": "no", "metadata": {"lex": "bok_1"}},
        ],
    }
    parsed = _inline_adapter.validate_python(original)
    dumped = parsed.model_dump(mode="json")
    assert dumped == original


# ---------------------------------------------------------------------------
# Export strip: per-kind projection
# ---------------------------------------------------------------------------


def _lesson_with_section_spans(spans: list[dict]) -> dict:
    """Minimal valid lesson with one section whose paragraph carries the given spans."""
    return {
        "key": "span_strip_test",
        "concept_slug": "span_strip_test",
        "grounding_mode": "grounded",
        "title": "Span strip test",
        "cefr_level": "A1",
        "goal": "Test span strip.",
        "objectives": [{"id": "o1", "statement": "test", "bloom_targets": ["remember"]}],
        "elements": [
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Test",
                "blocks": [{"kind": "paragraph", "spans": spans}],
            }
        ],
        "review_pool": {"pools": [{"key": "o1", "objective_id": "o1", "cards": []}]},
    }


def test_export_strip_given_annotated_with_lang_no_expect_foreign_term():
    lesson = Lesson.model_validate(
        _lesson_with_section_spans([{"kind": "annotated", "value": "bok", "lang": "no", "metadata": {"lex": "bok_1"}}])
    )
    exported = to_export_dict(lesson)
    ExportedLesson.model_validate(exported)

    wire = exported["sections"][0]["blocks"][0]["spans"]
    assert wire == [{"kind": "foreign_term", "value": "bok", "lang": "no"}]


def test_export_strip_given_annotated_with_lang_en_expect_foreign_term():
    lesson = Lesson.model_validate(
        _lesson_with_section_spans(
            [{"kind": "annotated", "value": "the book", "lang": "en", "metadata": {"meaning": "book"}}]
        )
    )
    exported = to_export_dict(lesson)
    wire = exported["sections"][0]["blocks"][0]["spans"]
    assert wire == [{"kind": "foreign_term", "value": "the book", "lang": "en"}]


def test_export_strip_given_annotated_without_lang_expect_text():
    lesson = Lesson.model_validate(
        _lesson_with_section_spans([{"kind": "annotated", "value": "word", "metadata": {"note": "x"}}])
    )
    exported = to_export_dict(lesson)
    wire = exported["sections"][0]["blocks"][0]["spans"]
    assert wire == [{"kind": "text", "value": "word"}]


def test_export_strip_given_sentence_expect_children_inlined_and_recursively_stripped():
    lesson = Lesson.model_validate(
        _lesson_with_section_spans(
            [
                {"kind": "text", "value": "before "},
                {
                    "kind": "sentence",
                    "metadata": {"tense": "past"},
                    "children": [
                        {"kind": "text", "value": "a "},
                        {"kind": "annotated", "value": "huset", "lang": "no", "metadata": {"lex": "hus_1"}},
                    ],
                },
            ]
        )
    )
    exported = to_export_dict(lesson)
    ExportedLesson.model_validate(exported)

    wire = exported["sections"][0]["blocks"][0]["spans"]
    assert wire == [
        {"kind": "text", "value": "before "},
        {"kind": "text", "value": "a "},
        {"kind": "foreign_term", "value": "huset", "lang": "no"},
    ]


def test_export_strip_given_mixed_kinds_expect_no_internal_kinds_on_wire():
    spans = [
        {"kind": "text", "value": "T "},
        {"kind": "emphasis", "value": "E "},
        {"kind": "strong", "value": "S "},
        {"kind": "code", "value": "C "},
        {"kind": "foreign_term", "value": "F", "lang": "no"},
        {"kind": "annotated", "value": "A", "lang": "no", "metadata": {"lex": "a_1"}},
        {"kind": "sentence", "children": [{"kind": "text", "value": "inner"}]},
    ]
    lesson = Lesson.model_validate(_lesson_with_section_spans(spans))
    exported = to_export_dict(lesson)

    wire = exported["sections"][0]["blocks"][0]["spans"]
    for span in wire:
        assert span["kind"] in _WIRE_KINDS
        assert "metadata" not in span
