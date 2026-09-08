"""Entry point: tests for the source prose parser (`parse_prose`).

Covers all 10 block kinds, the 7 span kinds, text merge, NFC, -smart,
data-* normalization, sentence containers, annotated spans, fail-closed
errors (links, multi-block items, missing frontmatter), and registry warnings.
"""

from __future__ import annotations

import pytest

from tests.application.operations.utils import K_TEST_FM
from tests.application.operations.utils import lesson_source
from tests.application.operations.utils import parse_prose
from tests.application.operations.utils import require_block

# ── Frontmatter ──────────────────────────────────────────────────────────


def test_parse_prose_given_valid_frontmatter_expect_typed_model():
    result = parse_prose(lesson_source("Some text."))
    assert result.frontmatter.slug == "test_lesson"
    assert result.frontmatter.cefr_level == "A1"
    assert result.frontmatter.default_lang == "nb"
    assert result.frontmatter.objectives[0].id == "o1"


def test_parse_prose_given_missing_required_frontmatter_field_expect_value_error():
    bad_fm = """\
---
type: Lesson
slug: test_lesson
title: Test Lesson
cefr_level: A1
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand]
objectives:
  - id: o1
    statement: Test objective.
    bloom_targets: [understand]
requirements_ref: curriculum/concepts/test.md
---

"""
    with pytest.raises(ValueError, match="Frontmatter validation failed"):
        parse_prose(bad_fm)


def test_parse_prose_given_unknown_frontmatter_field_expect_value_error():
    bad_fm = K_TEST_FM.replace(
        "requirements_ref: curriculum/concepts/test.md\n",
        "requirements_ref: curriculum/concepts/test.md\nbogus_field: oops\n",
    )
    with pytest.raises(ValueError, match="Frontmatter validation failed"):
        parse_prose(bad_fm)


def test_parse_prose_given_duplicate_frontmatter_key_expect_value_error():
    bad_fm = K_TEST_FM.replace("slug: test_lesson\n", "slug: test_lesson\nslug: overwritten\n")

    with pytest.raises(ValueError, match="duplicate YAML key"):
        parse_prose(bad_fm)


# ── Block kind: heading ──────────────────────────────────────────────────


def test_parse_prose_given_h2_heading_with_role_prefix_expect_heading_level_3():
    result = parse_prose(lesson_source("## orient: Introduction {#sec-intro}\n"))
    block = require_block(result)
    assert block.kind == "heading"
    assert block.level == 3
    assert len(block.spans) == 1
    assert block.spans[0].kind == "text"
    assert block.spans[0].value == "Introduction"


def test_parse_prose_given_h3_heading_expect_heading_level_4():
    result = parse_prose(lesson_source("### A Sub Heading {#sec-sub}\n"))
    block = require_block(result)
    assert block.kind == "heading"
    assert block.level == 4
    assert block.spans[0].value == "A Sub Heading"


def test_parse_prose_given_h1_heading_expect_value_error():
    with pytest.raises(ValueError, match="level 1.*reserved"):
        parse_prose(lesson_source("# Title\n"))


# ── Block kind: paragraph ────────────────────────────────────────────────


def test_parse_prose_given_plain_paragraph_expect_paragraph_block():
    result = parse_prose(lesson_source("Hello world.\n"))
    block = require_block(result)
    assert block.kind == "paragraph"
    assert len(block.spans) == 1
    assert block.spans[0].kind == "text"
    assert block.spans[0].value == "Hello world."


# ── Block kind: reading ──────────────────────────────────────────────────


def test_parse_prose_given_reading_div_with_translation_expect_reading_metadata():
    body = '::: {.reading translation="Hello, Nora."}\nHei, Nora.\n:::\n'
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "reading"
    assert block.translation == "Hello, Nora."
    assert block.spans[0].value == "Hei, Nora."


def test_parse_prose_given_reading_character_id_expect_recurring_metadata():
    body = (
        '::: {.reading translation="Hello, Anna." dialogue_id="d1" '
        'speaker_id="anna-local" speaker_name="Anna" character_id="anna"}\n'
        "Hei, Anna.\n:::\n"
    )
    result = parse_prose(lesson_source(body))
    block = require_block(result)

    assert block.character_id == "anna"
    assert block.speaker_id == "anna-local"


def test_parse_prose_given_reading_voice_profile_expect_profile_metadata():
    body = (
        '::: {.reading translation="Are you going to Lillehammer?" dialogue_id="d1" '
        'speaker_id="erik" speaker_name="Erik" voice_profile="masculine"}\n'
        "Skal du til Lillehammer?\n:::\n"
    )
    result = parse_prose(lesson_source(body))
    block = require_block(result)

    assert block.voice_profile == "masculine"
    assert block.character_id is None


def test_parse_prose_given_reading_div_without_translation_expect_value_error():
    body = "::: {.reading}\nHei.\n:::\n"
    with pytest.raises(ValueError, match="non-empty translation"):
        parse_prose(lesson_source(body))


# ── Block kind: rule ─────────────────────────────────────────────────────


def test_parse_prose_given_rule_div_expect_rule_block():
    result = parse_prose(lesson_source("::: rule\nAdjectives agree with nouns.\n:::\n"))
    block = require_block(result)
    assert block.kind == "rule"
    assert block.statement[0].value == "Adjectives agree with nouns."


# ── Block kind: example ──────────────────────────────────────────────────


def test_parse_prose_given_example_div_expect_example_block():
    body = "::: example\n- no: Nora har en bok.\n- en: Nora has a book.\n:::\n"
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "example"
    no_text = " ".join(s.value for s in block.no if s.kind == "text")
    en_text = " ".join(s.value for s in block.en if s.kind == "text")
    assert "Nora har en bok." in no_text
    assert "Nora has a book." in en_text


# ── Block kind: examples ─────────────────────────────────────────────────


def test_parse_prose_given_examples_div_expect_examples_block():
    body = "::: examples\n- no: Norsk 1.\n- en: English 1.\n- no: Norsk 2.\n- en: English 2.\n:::\n"
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "examples"
    assert len(block.items) == 2


# ── Block kind: word_list ────────────────────────────────────────────────


def test_parse_prose_given_word_list_div_expect_word_list_block():
    body = "::: word_list\n- term: bok\n  form: noun\n- term: hus\n  form: noun\n:::\n"
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "word_list"
    assert block.items[0].term == "bok"
    assert block.items[0].form == "noun"
    assert block.items[1].term == "hus"


# ── Block kind: table ────────────────────────────────────────────────────


def test_parse_prose_given_table_in_col_langs_div_expect_table_block():
    body = '::: {col_langs="nb,en"}\n| Gender | Form |\n|---|---|\n| en | bil |\n| ei | dør |\n:::\n'
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "table"
    assert block.col_langs == ["no", "en"]
    assert len(block.headers) == 2
    assert block.headers[0][0].value == "Gender"
    assert len(block.rows) == 2
    assert block.rows[0][0][0].value == "en"
    assert block.rows[0][1][0].value == "bil"


# ── Block kind: callout ──────────────────────────────────────────────────


def test_parse_prose_given_callout_div_expect_recursive_blocks():
    body = "::: {.callout variant=tip}\n**Important:** Remember this.\n\nMore text.\n:::\n"
    result = parse_prose(lesson_source(body))
    block = require_block(result)
    assert block.kind == "callout"
    assert block.level == "tip"
    assert len(block.blocks) == 2
    assert block.blocks[0].kind == "paragraph"
    assert block.blocks[1].kind == "paragraph"


def test_parse_prose_given_callout_with_invalid_variant_expect_value_error():
    body = "::: {.callout variant=bogus}\nText.\n:::\n"
    with pytest.raises(ValueError, match="variant must be one of"):
        parse_prose(lesson_source(body))


# ── Block kind: list ─────────────────────────────────────────────────────


def test_parse_prose_given_bullet_list_expect_list_block():
    result = parse_prose(lesson_source("- First item.\n- Second item.\n"))
    block = require_block(result)
    assert block.kind == "list"
    assert block.ordered is False
    assert len(block.items) == 2
    assert block.items[0][0].value == "First item."


def test_parse_prose_given_ordered_list_expect_ordered_list_block():
    result = parse_prose(lesson_source("1. First item.\n2. Second item.\n"))
    block = require_block(result)
    assert block.kind == "list"
    assert block.ordered is True


def test_parse_prose_given_multi_block_list_item_expect_value_error():
    body = "- First paragraph.\n\n  Second paragraph in same item.\n"
    with pytest.raises(ValueError, match="Multi-block"):
        parse_prose(lesson_source(body))


# ── Span kind: annotated ─────────────────────────────────────────────────


def test_parse_prose_given_bracketed_lex_span_expect_annotated():
    result = parse_prose(lesson_source("Text with [bok]{lex=bok_1} inline.\n"))
    para = require_block(result)
    annotated = [s for s in para.spans if s.kind == "annotated"]
    assert len(annotated) == 1
    assert annotated[0].value == "bok"
    assert annotated[0].metadata == {"lex": "bok_1"}
    assert annotated[0].lang is None


# ── Span kind: data-* normalization ──────────────────────────────────────


def test_parse_prose_given_html_data_lex_span_expect_annotated_with_data_prefix_stripped():
    body = 'Text with <span data-lex="bok_1">bok</span> inline.\n'
    result = parse_prose(lesson_source(body))
    para = require_block(result)
    annotated = [s for s in para.spans if s.kind == "annotated"]
    assert len(annotated) == 1
    assert annotated[0].value == "bok"
    assert annotated[0].metadata == {"lex": "bok_1"}


# ── Span kind: sentence ──────────────────────────────────────────────────


def test_parse_prose_given_tense_sentence_expect_sentence_container_with_children():
    body = "[I går lånte Nora en bok.]{tense=past}\n"
    result = parse_prose(lesson_source(body))
    para = require_block(result)
    sentences = [s for s in para.spans if s.kind == "sentence"]
    assert len(sentences) == 1
    assert sentences[0].metadata == {"tense": "past"}
    assert len(sentences[0].children) >= 1
    assert all(c.kind in ("text", "annotated") for c in sentences[0].children)


def test_parse_prose_given_tense_sentence_with_inner_lex_expect_sentence_with_word_spans():
    body = "[Nora leser en [bok]{lex=bok_1}.]{tense=past}\n"
    result = parse_prose(lesson_source(body))
    para = require_block(result)
    sentence = [s for s in para.spans if s.kind == "sentence"][0]
    annotated_children = [c for c in sentence.children if c.kind == "annotated"]
    assert len(annotated_children) == 1
    assert annotated_children[0].metadata == {"lex": "bok_1"}


# ── Span kind: foreign_term ──────────────────────────────────────────────


def test_parse_prose_given_lang_only_span_expect_foreign_term():
    result = parse_prose(lesson_source("Text with [bok]{lang=nb} inline.\n"))
    para = require_block(result)
    foreign = [s for s in para.spans if s.kind == "foreign_term"]
    assert len(foreign) == 1
    assert foreign[0].value == "bok"
    assert foreign[0].lang == "no"


# ── Span kind: emphasis, strong, code ────────────────────────────────────


def test_parse_prose_given_emphasis_strong_code_expect_correct_span_kinds():
    result = parse_prose(lesson_source("This is *emphasized*, **strong**, and `code`.\n"))
    para = require_block(result)
    kinds = {s.kind for s in para.spans}
    assert "emphasis" in kinds
    assert "strong" in kinds
    assert "code" in kinds


# ── Span: text merge ─────────────────────────────────────────────────────


def test_parse_prose_given_adjacent_text_elements_expect_single_merged_text_span():
    result = parse_prose(lesson_source("Hello world this is text.\n"))
    para = require_block(result)
    text_spans = [s for s in para.spans if s.kind == "text"]
    assert len(text_spans) == 1
    assert text_spans[0].value == "Hello world this is text."


# ── Span: BLANK marker ───────────────────────────────────────────────────


def test_parse_prose_given_blank_literal_expect_text_span_preserving_marker():
    result = parse_prose(lesson_source("[BLANK]\n"))
    para = require_block(result)
    assert len(para.spans) == 1
    assert para.spans[0].kind == "text"
    assert para.spans[0].value == "[BLANK]"


# ── Span: -smart preserves apostrophes ───────────────────────────────────


def test_parse_prose_given_straight_apostrophe_expect_preserved_not_smart_quoted():
    result = parse_prose(lesson_source("Don't worry, it's fine.\n"))
    para = require_block(result)
    text_values = [s.value for s in para.spans if s.kind == "text"]
    combined = "".join(text_values)
    assert "'" in combined
    assert "\u2019" not in combined


# ── Registry: unknown attr warning ───────────────────────────────────────


def test_parse_prose_given_unknown_attr_key_expect_warning_with_typo_suggestion_and_preserved():
    body = "Text with [word]{lexn=bok_1} inline.\n"
    result = parse_prose(lesson_source(body))
    assert len(result.warnings) >= 1
    warning = result.warnings[0]
    assert "lexn" in warning
    assert "lex" in warning  # typo suggestion
    para = require_block(result)
    annotated = [s for s in para.spans if s.kind == "annotated"]
    assert annotated[0].metadata.get("lexn") == "bok_1"


# ── Fail-closed: metadata-bearing link ───────────────────────────────────


def test_parse_prose_given_link_in_prose_expect_type_error():
    body = "This is a [link](http://example.com) here.\n"
    with pytest.raises(TypeError, match="Link.*not allowed"):
        parse_prose(lesson_source(body))


# ── Fail-closed: metadata-bearing image ──────────────────────────────────


def test_parse_prose_given_image_in_prose_expect_type_error():
    body = "This is an ![image](http://example.com/img.png) here.\n"
    with pytest.raises(TypeError, match="Image.*not allowed"):
        parse_prose(lesson_source(body))


# ── Fail-closed: raw inline HTML ─────────────────────────────────────────


def test_parse_prose_given_raw_inline_html_expect_value_error():
    body = "Text with <b>bold</b> inline.\n"
    with pytest.raises(ValueError, match="[Rr]aw.*not allowed"):
        parse_prose(lesson_source(body))


# ── Fingerprint ──────────────────────────────────────────────────────────


def test_parse_prose_given_any_input_expect_fingerprint_has_pandoc_panflute_and_reader():
    result = parse_prose(lesson_source("Text.\n"))
    fp = result.fingerprint
    assert "pandoc" in fp
    assert "panflute" in fp
    assert fp["reader"] == "markdown+native_spans+bracketed_spans-smart"


# ── Post-parse invariants ────────────────────────────────────────────────


def test_parse_prose_given_nested_lex_spans_expect_value_error():
    """An annotated span with ``lex`` must not contain another lex span."""
    body = "[outer [inner]{lex=inner_1} text]{lex=outer_1}\n"
    with pytest.raises(ValueError, match="[Nn]ested lex"):
        parse_prose(lesson_source(body))


# ── Round-trip: all 10 block kinds in one document ───────────────────────


def test_parse_prose_given_all_ten_block_kinds_expect_all_parsed():
    body = (
        "## orient: Title {#sec-1}\n\n"
        "Plain paragraph.\n\n"
        "::: rule\nRule text.\n:::\n\n"
        "::: example\n- no: Norsk.\n- en: English.\n:::\n\n"
        "::: examples\n- no: N1.\n- en: E1.\n:::\n\n"
        "::: word_list\n- term: bok\n  form: noun\n:::\n\n"
        '::: {col_langs="nb,en"}\n| A | B |\n|---|---|\n| 1 | 2 |\n:::\n\n'
        '::: {.reading translation="Hello."}\nHei.\n:::\n\n'
        "::: {.callout variant=note}\nNote text.\n:::\n\n"
        "- List item.\n"
    )
    result = parse_prose(lesson_source(body))
    kinds = [b.kind for b in result.blocks]
    assert kinds == [
        "heading",
        "paragraph",
        "rule",
        "example",
        "examples",
        "word_list",
        "table",
        "reading",
        "callout",
        "list",
    ]
