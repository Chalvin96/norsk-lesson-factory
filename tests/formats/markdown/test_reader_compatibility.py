"""Entry point: `parse_markdown` reader contract, including the accepted CommonMark differences."""

from __future__ import annotations

import panflute
import pytest

from lesson_builder.application.operations.load_lesson import load_lesson_source
from lesson_builder.formats.markdown.pandoc import parse_markdown
from lesson_builder.formats.markdown.pandoc_blocks import convert_blocks
from tests.application.operations.utils import K_TEST_EXERCISES_SOURCE
from tests.application.operations.utils import K_TEST_LESSON_SOURCE


def test_parse_markdown_given_attributed_span_expect_preserved_language_and_annotation():
    doc = parse_markdown("[ord]{lang=nb lex=word}")

    span = doc.content[0].content[0]
    assert isinstance(span, panflute.Span)
    assert span.attributes == {"lang": "nb", "lex": "word"}
    assert panflute.stringify(span) == "ord"


def test_parse_markdown_given_inline_code_expect_code_node():
    doc = parse_markdown("`a < b`")

    assert isinstance(doc.content[0].content[0], panflute.Code)
    assert doc.content[0].content[0].text == "a < b"


def test_convert_blocks_given_wrapped_pipe_table_expect_language_scoped_table():
    doc = parse_markdown('::: {col_langs="nb,en"}\n| Ord | Word |\n|---|---|\n| bok | book |\n:::\n')

    blocks = convert_blocks(doc.content, [], "nb")

    assert blocks[0].kind == "table"
    assert blocks[0].col_langs == ["no", "en"]
    assert len(blocks[0].rows) == 1


@pytest.mark.parametrize(
    "spec,classes,attrs",
    [
        ("rule", ["rule"], {}),
        ("{.callout variant=warning}", ["callout"], {"variant": "warning"}),
        ('{#box .reading translation="hello"}', ["reading"], {"translation": "hello"}),
    ],
)
def test_parse_markdown_given_div_forms_expect_attributes_and_body(spec, classes, attrs):
    doc = parse_markdown(f"::: {spec}\nText.\n:::\n")

    div = doc.content[0]
    assert isinstance(div, panflute.Div)
    assert list(div.classes) == classes
    assert div.attributes == attrs
    assert panflute.stringify(div).strip() == "Text."


@pytest.mark.parametrize(
    "marker,delimiter,start",
    [("1.", "Period", 1), ("3)", "OneParen", 3)],
)
def test_parse_markdown_given_decimal_list_markers_expect_ordered_list(marker, delimiter, start):
    doc = parse_markdown(f"{marker}  First\n")

    ordered = doc.content[0]
    assert isinstance(ordered, panflute.OrderedList)
    assert (ordered.delimiter, ordered.start) == (delimiter, start)


@pytest.mark.parametrize(
    "source,node,error",
    [
        ("[word](https://example.invalid)", panflute.Link, TypeError),
        ("[word](javascript:alert(1))", panflute.Link, TypeError),
        ("a ![word](data:image/svg+xml;base64,AA)", panflute.Image, TypeError),
        ("a ![word](image.png)", panflute.Image, TypeError),
        ("a <b>word</b>", panflute.RawInline, ValueError),
        ('a <span lang="nb">ord</span>', panflute.RawInline, ValueError),
        ("a  \nb", panflute.LineBreak, ValueError),
        ("a\\\nb", panflute.LineBreak, ValueError),
    ],
)
def test_convert_blocks_given_forbidden_inline_expect_mapper_rejection(source, node, error):
    doc = parse_markdown(source)
    seen = []
    doc.walk(lambda element, _: seen.append(element) if isinstance(element, node) else None)

    assert seen
    with pytest.raises(error):
        convert_blocks(doc.content, [], "nb")


@pytest.mark.parametrize(
    "attribute,expected",
    [
        (r'translation="C:\temp"', r"C:\temp"),
        (r'translation="say \"hi\""', 'say "hi"'),
        ('translation="a &amp; b"', "a & b"),
        (r"translation='it\'s fine'", "it's fine"),
        ('translation="{inside}"', "{inside}"),
        ('translation=""', ""),
        ("translation=", ""),
    ],
)
@pytest.mark.parametrize("wrapper", ["div", "span", "header"])
def test_parse_markdown_given_attribute_edge_cases_expect_pandoc_value(attribute, expected, wrapper):
    sources = {
        "div": f"::: {{.reading {attribute}}}\nHei.\n:::\n",
        "span": f"[Hei.]{{{attribute}}}",
        "header": f"## Hei. {{#sec {attribute}}}",
    }
    doc = parse_markdown(sources[wrapper])

    element = doc.content[0].content[0] if wrapper == "span" else doc.content[0]
    assert element.attributes["translation"] == expected
    assert panflute.stringify(element).strip() == "Hei."


@pytest.mark.parametrize(
    "spec",
    ['{- #sec x:y=one data-value=two title="{braces}"}', '{#sec .unnumbered x:y=one data-value=two title="{braces}"}'],
)
def test_parse_markdown_given_header_attributes_expect_explicit_identity_and_values(spec):
    doc = parse_markdown(f"## Title {spec}\n")

    header = doc.content[0]
    assert header.identifier == "sec"
    assert "unnumbered" in header.classes
    assert header.attributes == {"x:y": "one", "data-value": "two", "title": "{braces}"}
    assert panflute.stringify(header).strip() == "Title"


def test_parse_markdown_given_escaped_space_and_quotes_expect_nbsp_and_straight_quotes():
    doc = parse_markdown('a\\ b "plain" -- ...')

    assert panflute.stringify(doc).strip() == 'a\u00a0b "plain" -- ...'


def test_load_lesson_source_given_missing_explicit_header_id_expect_error():
    source = K_TEST_LESSON_SOURCE.replace("{#sec-intro objectives=obj_greet}", "{objectives=obj_greet}")

    with pytest.raises(ValueError, match="explicit"):
        load_lesson_source(source, K_TEST_EXERCISES_SOURCE)


def test_parse_markdown_given_heading_without_blank_line_expect_new_heading():
    doc = parse_markdown("Text.\n## Title {#sec}\n")

    assert isinstance(doc.content[1], panflute.Header)


def test_parse_markdown_given_paired_intraword_blanks_expect_literal_underscores():
    doc = parse_markdown("B: ___. A: Kan ___")

    assert panflute.stringify(doc).strip() == "B: ___. A: Kan ___"
    assert all(isinstance(item, (panflute.Str, panflute.Space)) for item in doc.content[0].content)


def test_parse_markdown_given_list_directly_after_paragraph_expect_list():
    doc = parse_markdown("Text.\n- item\n")

    assert isinstance(doc.content[1], panflute.BulletList)


def test_parse_markdown_given_nested_divs_expect_recursive_callout():
    doc = parse_markdown("::: {.callout variant=note}\n::: {.rule}\nRemember.\n:::\n\nAfter.\n:::\n")

    blocks = convert_blocks(doc.content, [], "nb")

    assert blocks[0].kind == "callout"
    assert [block.kind for block in blocks[0].blocks] == ["rule", "paragraph"]


@pytest.mark.parametrize(
    "attribute,expected",
    [
        (r'translation="a\\b"', r"a\b"),
        (r'translation="a\*b"', "a*b"),
        (r'translation="a\ b"', "a b"),
    ],
)
def test_parse_markdown_given_attribute_escapes_expect_punctuation_unescaped(attribute, expected):
    doc = parse_markdown(f"::: {{.reading {attribute}}}\nHei.\n:::\n")

    assert doc.content[0].attributes["translation"] == expected


def test_parse_markdown_given_malformed_attributes_expect_literal_text():
    doc = parse_markdown("[ord]{bad}")

    assert panflute.stringify(doc).strip() == "[ord]{bad}"
    assert not any(isinstance(item, panflute.Span) for item in doc.content[0].content)


@pytest.mark.parametrize("source", ["a. one", "i. dag", "IV) four", "vi."])
def test_parse_markdown_given_letter_or_roman_marker_expect_paragraph_text(source):
    doc = parse_markdown(source)

    assert isinstance(doc.content[0], panflute.Para)
    assert panflute.stringify(doc).strip() == source
