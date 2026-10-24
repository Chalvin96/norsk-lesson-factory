"""Entry points: regression tests for concurrent parsing and rejected Markdown syntax."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import panflute
import pytest

from lesson_builder.formats.markdown.markdown_rules import create_markdown_reader
from lesson_builder.formats.markdown.pandoc import parse_markdown
from lesson_builder.formats.markdown.pandoc_blocks import convert_blocks


def test_parse_given_concurrent_first_reads_expect_serial_results():
    source = "## Title {#sec}\n\nA **bold** and *plain* paragraph.\n\n- first\n- second\n"
    expected = [token.as_dict() for token in create_markdown_reader().parse(source)]
    previous_interval = sys.getswitchinterval()
    results = []
    try:
        sys.setswitchinterval(1e-5)
        with ThreadPoolExecutor(max_workers=16) as pool:
            for _ in range(100):
                reader = create_markdown_reader()
                barrier = Barrier(16)

                def parse_together(_, barrier=barrier, reader=reader):
                    barrier.wait()
                    return [token.as_dict() for token in reader.parse(source)]

                results.extend(pool.map(parse_together, range(16)))
    finally:
        sys.setswitchinterval(previous_interval)

    assert all(result == expected for result in results)


@pytest.mark.parametrize(
    "source",
    ["Mm. Det høres bra ut.", "Hun heter\nLiv. Hun bor i Bergen.", "Han sa\nVi. Vi kom"],
)
def test_parse_markdown_given_roman_letter_words_expect_intact_paragraph(source):
    doc = parse_markdown(source)

    assert len(doc.content) == 1
    assert isinstance(doc.content[0], panflute.Para)
    assert panflute.stringify(doc).strip() == source.replace("\n", " ")


def test_parse_markdown_given_uppercase_roman_word_expect_paragraph_text():
    doc = parse_markdown("MM. x")

    assert isinstance(doc.content[0], panflute.Para)
    assert panflute.stringify(doc).strip() == "MM. x"


@pytest.mark.parametrize("marker", ["a.", "i.", "ii.", "A)", "I)", "II)", "2.", "2)"])
def test_parse_markdown_given_nondecimal_or_nonfirst_marker_after_text_expect_one_paragraph(marker):
    source = f"Text.\n{marker}  item"
    doc = parse_markdown(source)

    assert len(doc.content) == 1
    assert isinstance(doc.content[0], panflute.Para)
    assert panflute.stringify(doc).strip() == source.replace("  ", " ").replace("\n", " ")


def test_convert_blocks_given_strikethrough_expect_mapper_rejection():
    doc = parse_markdown("~~strike~~")

    assert isinstance(doc.content[0].content[0], panflute.Strikeout)
    with pytest.raises(ValueError, match="Unsupported inline element Strikeout"):
        convert_blocks(doc.content, [], "nb")


@pytest.mark.parametrize("source", ["Text.^[footnote]", "Text.[^1]\n\n[^1]: footnote", "Text.[^1]\n\n[^1]: self[^1]"])
def test_convert_blocks_given_footnote_expect_mapper_rejection(source):
    doc = parse_markdown(source)

    assert isinstance(doc.content[0].content[-1], panflute.Note)
    with pytest.raises(ValueError, match="Unsupported inline element Note"):
        convert_blocks(doc.content, [], "nb")


def test_convert_blocks_given_footnote_reference_in_heading_expect_mapper_rejection():
    doc = parse_markdown("## Title[^1] {#sec}\n\n[^1]: footnote words")

    assert any(isinstance(item, panflute.Note) for item in doc.content[0].content)
    with pytest.raises(ValueError, match="Note"):
        convert_blocks(doc.content, [], "nb")


def test_convert_blocks_given_html_span_expect_bracketed_span_hint():
    doc = parse_markdown('a <span lang="nb">ord</span>')

    with pytest.raises(ValueError, match=r"use \[text\]\{attributes\}"):
        convert_blocks(doc.content, [], "nb")


def test_convert_blocks_given_definition_list_expect_mapper_rejection():
    doc = parse_markdown("Term\n:   Definition\n")

    assert isinstance(doc.content[0], panflute.DefinitionList)
    with pytest.raises(ValueError, match="DefinitionList"):
        convert_blocks(doc.content, [], "nb")
