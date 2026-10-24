"""Entry point: `parse_markdown` returns panflute documents and attributed spans."""

from __future__ import annotations

import panflute

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


def test_markdown_import_given_uncached_panflute_expect_only_dependency_escape_warning_suppressed(tmp_path) -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-X",
            f"pycache_prefix={tmp_path / 'cache'}",
            "-W",
            "always::SyntaxWarning",
            "-c",
            "from lesson_builder.formats.markdown.pandoc import parse_markdown; "
            "import warnings; "
            "warnings.warn_explicit(\"invalid escape sequence '\\\\*'\", SyntaxWarning, 'other.py', 1, module='other'); "
            "warnings.warn_explicit('unrelated panflute warning', SyntaxWarning, 'panflute/io.py', 1, module='panflute.io'); "
            "assert parse_markdown('hello').content",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "site-packages/panflute/io.py" not in result.stderr
    assert "other.py:1: SyntaxWarning: invalid escape sequence" in result.stderr
    assert "unrelated panflute warning" in result.stderr
