"""Entry point: `parse_markdown` behavior at the pinned pandoc boundary.

Runs the real pinned pypandoc reader offline (pandoc is a dev dependency).
Source parsing, frontmatter, and lesson projection are covered by the source
suite; these tests own the adapter contract: the pinned reader, the compiler
fingerprint, and the doc block helper.
"""

from __future__ import annotations

import panflute

from lesson_builder.formats.markdown.pandoc import compiler_fingerprint
from lesson_builder.formats.markdown.pandoc import parse_markdown


def test_parse_markdown_given_markdown_text_expect_panflute_doc_with_content():
    doc = parse_markdown("Plain paragraph.\n")

    assert isinstance(doc, panflute.Doc)
    assert len(doc.content) == 1
    assert isinstance(doc.content[0], panflute.Para)


def test_parse_markdown_given_bracketed_span_expect_pinned_reader_parsed_it():
    doc = parse_markdown("En [spenn]{lex=lex_1} tekst.\n")

    spans = list(doc.content[0].content) if doc.content else []
    assert any(isinstance(span, panflute.Span) for span in spans)


def test_compiler_fingerprint_given_any_input_expect_pandoc_panflute_and_reader_versions():
    fingerprint = compiler_fingerprint()

    assert set(fingerprint) == {"pandoc", "panflute", "reader"}
    assert fingerprint["pandoc"]
    assert fingerprint["panflute"]
    assert fingerprint["reader"] == "markdown+native_spans+bracketed_spans-smart"
