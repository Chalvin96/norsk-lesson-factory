"""Entry point: `convert_inlines` converts panflute inline elements into ``list[InlineSpan]``.

Handles the seven internal span kinds (text, emphasis, strong, code,
foreign_term, annotated, sentence), text merging, NFC normalization,
``data-*`` prefix stripping, ``[BLANK]`` literals, and fail-closed rejection
of metadata-bearing Link/Image/RawInline.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import panflute

from lesson_builder.domain.lesson.models.inline import AnnotatedSpan
from lesson_builder.domain.lesson.models.inline import CodeSpan
from lesson_builder.domain.lesson.models.inline import EmphasisSpan
from lesson_builder.domain.lesson.models.inline import ForeignTermSpan
from lesson_builder.domain.lesson.models.inline import InlineSpan
from lesson_builder.domain.lesson.models.inline import SentenceSpan
from lesson_builder.domain.lesson.models.inline import StrongSpan
from lesson_builder.domain.lesson.models.inline import TextSpan
from lesson_builder.formats.markdown.attributes import normalize_text
from lesson_builder.formats.markdown.attributes import validate_attributes

K_SPANS_BLANK_MARKER = "[BLANK]"
K_SPANS_SNIPPET_LIMIT = 60


def convert_inlines(
    elements: Iterable[Any],
    warnings: list[str],
    default_lang: str = "nb",
) -> list[InlineSpan]:
    """Convert a sequence of panflute inline elements into ``list[InlineSpan]``.

    Adjacent text-producing elements (Str, Space, SoftBreak) are merged into
    single ``TextSpan`` values. All text is NFC-normalized. ``Link``,
    ``Image``, and ``RawInline`` are rejected (fail closed).
    """
    spans: list[InlineSpan] = []
    text_buffer: list[str] = []

    for el in elements:
        if _append_text_inline(el, text_buffer):
            continue
        spans.extend(_convert_non_text_inline(el, spans, text_buffer, warnings, default_lang))

    _flush_text(spans, text_buffer)
    return spans


def stringify_inlines(elements: Iterable[Any]) -> str:
    """Flatten panflute inline elements into a plain string."""
    parts: list[str] = []
    for element in elements:
        if isinstance(element, panflute.Str):
            parts.append(element.text)
        elif isinstance(element, (panflute.Space, panflute.SoftBreak)):
            parts.append(" ")
        elif isinstance(element, (panflute.Emph, panflute.Strong, panflute.Span)):
            parts.append(stringify_inlines(element.content))
        elif isinstance(element, panflute.Code):
            parts.append(element.text)
    return "".join(parts)


def _append_text_inline(element: object, buffer: list[str]) -> bool:
    """Append a plain-text panflute inline and report whether it was handled."""
    if isinstance(element, panflute.Str):
        buffer.append(element.text)
        return True
    if isinstance(element, (panflute.Space, panflute.SoftBreak)):
        buffer.append(" ")
        return True
    return False


def _convert_non_text_inline(
    element: object,
    spans: list[InlineSpan],
    buffer: list[str],
    warnings: list[str],
    default_lang: str,
) -> list[InlineSpan]:
    """Convert one non-text inline, flushing buffered plain text first."""
    _flush_text(spans, buffer)
    if isinstance(element, panflute.Emph):
        return [EmphasisSpan(kind="emphasis", value=normalize_text(stringify_inlines(element.content)))]
    if isinstance(element, panflute.Strong):
        return [StrongSpan(kind="strong", value=normalize_text(stringify_inlines(element.content)))]
    if isinstance(element, panflute.Code):
        return [CodeSpan(kind="code", value=normalize_text(element.text))]
    if isinstance(element, panflute.Span):
        return _convert_span(element, warnings, default_lang)
    if isinstance(element, (panflute.Link, panflute.Image)):
        raise TypeError(
            f"Metadata-bearing {type(element).__name__} is not allowed in prose. Snippet: {_snippet(element)}"
        )
    if isinstance(element, panflute.RawInline):
        _reject_raw_inline(element)
    raise ValueError(f"Unsupported inline element {type(element).__name__} in prose. Snippet: {_snippet(element)}")


def _reject_raw_inline(element: object) -> None:
    """Raise the appropriate error for raw HTML encountered in prose."""
    if _is_approved_span_raw(element):
        raise ValueError(f"Raw HTML span must be parsed with native_spans enabled. Snippet: {_snippet(element)}")
    raise ValueError(f"Raw inline HTML is not allowed in prose. Snippet: {_snippet(element)}")


def _convert_span(
    el: panflute.Span,
    warnings: list[str],
    default_lang: str,
) -> list[InlineSpan]:
    """Convert a panflute ``Span`` into one or more ``InlineSpan`` values."""
    attrs: dict[str, str] = dict(el.attributes)
    snippet = f"span [{stringify_inlines(el.content)}]"

    if not attrs:
        return convert_inlines(el.content, warnings, default_lang)

    result = validate_attributes(attrs, snippet)
    warnings.extend(result.warnings)

    child_spans = convert_inlines(el.content, warnings, default_lang)

    # Sentence container when sentence-level keys are present.
    if result.sentence_keys:
        metadata = dict(result.metadata)
        metadata.update(result.sentence_keys)
        return [SentenceSpan(kind="sentence", children=child_spans, metadata=metadata)]

    # Determine value: merge child text for annotated/foreign_term leaf spans.
    value = _merge_span_values(child_spans)

    lang = result.lang
    has_metadata = bool(result.metadata)

    if has_metadata:
        # Post-parse invariant: no nested lex spans (§4.2). If the outer span
        # carries lex, inner annotated spans must not also carry lex.
        if "lex" in result.metadata:
            _reject_inner_lex(child_spans, snippet)
        return [
            AnnotatedSpan(
                kind="annotated",
                value=value,
                lang=lang,
                metadata=result.metadata,
            )
        ]

    if lang is not None:
        return [ForeignTermSpan(kind="foreign_term", value=value, lang=lang)]

    # Span with attrs but all consumed (e.g., only data-* that normalized away).
    # Treat as annotated with empty metadata.
    return [AnnotatedSpan(kind="annotated", value=value, lang=None, metadata={})]


def _flush_text(spans: list[InlineSpan], buffer: list[str]) -> None:
    """Flush the text buffer as a single ``TextSpan`` (NFC-normalized)."""
    if not buffer:
        return
    merged = normalize_text("".join(buffer))
    if merged:
        spans.append(TextSpan(kind="text", value=merged))
    buffer.clear()


def _span_text(span: InlineSpan) -> str:
    """Extract the text value from a leaf span, or recurse for sentence."""
    if span.kind == "sentence":
        return _merge_span_values(span.children)
    return span.value


def _merge_span_values(spans: list[InlineSpan]) -> str:
    """Merge a list of internal spans back into a single text value."""
    return "".join(_span_text(span) for span in spans)


def _reject_inner_lex(spans: list[InlineSpan], snippet: str) -> None:
    """Reject ``lex`` annotations nested inside a ``lex``-bearing span (§4.2).

    This catches ``[outer [inner]{lex=x} text]{lex=y}`` -- an annotated span
    with ``lex`` that contains a child annotated span with ``lex``. Sentence
    containers may hold word spans (including lex); this check only fires for
    leaf annotated spans.
    """
    for span in spans:
        if span.kind == "annotated" and "lex" in span.metadata:
            raise ValueError(
                f"Nested lex spans are not allowed -- a lex-bearing span must not "
                f"contain another lex span. Snippet: {snippet}"
            )


def _is_approved_span_raw(el: object) -> bool:
    """Check if a RawInline is an HTML span (which should have been parsed)."""
    return bool(getattr(el, "format", "") == "html" and "<span" in getattr(el, "text", ""))


def _snippet(el: object) -> str:
    """Extract a short snippet string from a panflute element for error messages."""
    text = stringify_inlines([el]) if not isinstance(el, str) else el
    if len(text) > K_SPANS_SNIPPET_LIMIT:
        return text[:K_SPANS_SNIPPET_LIMIT] + "..."
    return text
