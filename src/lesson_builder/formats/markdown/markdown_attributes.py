"""Entry point: `parse_attributes` decodes authored attribute syntax."""

from __future__ import annotations

import html
import re
import string
from dataclasses import dataclass

K_MARKDOWN_ATTRIBUTE_TOKEN = re.compile(
    r"""\s*(?:\#(?P<id>[^\s}]+)|\.(?P<class>[^\s}]+)|(?P<unnumbered>-)(?=\s|})|(?P<key>[\w:-]+)=(?:"(?P<double>(?:\\.|[^"\\])*)"|'(?P<single>(?:\\.|[^'\\])*)'|(?P<bare>[^\s}]*)))"""
)
K_MARKDOWN_ATTRIBUTE_ESCAPE = re.compile(r"\\([" + re.escape(string.punctuation + " ") + r"])")


@dataclass(frozen=True)
class MarkdownAttributes:
    """Format-local attributes shared by block and inline token readers."""

    identifier: str
    classes: list[str]
    values: list[tuple[str, str]]

    def to_json(self) -> list[object]:
        """Return the Pandoc attribute triple without changing attribute order."""
        return [self.identifier, self.classes, self.values]


def parse_attributes(source: str, start: int = 0) -> tuple[MarkdownAttributes, int] | None:
    """Read one brace-delimited attribute set, retaining non-punctuation escapes."""
    if source[start : start + 1] != "{":
        return None
    identifier = ""
    classes: list[str] = []
    values: list[tuple[str, str]] = []
    pos = start + 1
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
            continue
        if source[pos] == "}":
            return MarkdownAttributes(identifier, classes, values), pos + 1
        match = K_MARKDOWN_ATTRIBUTE_TOKEN.match(source, pos)
        if match is None:
            return None
        pos = match.end()
        if match["id"] is not None:
            identifier = match["id"]
        elif match["class"] is not None:
            classes.append(match["class"])
        elif match["unnumbered"] is not None:
            classes.append("unnumbered")
        else:
            values.append((match["key"], _parse_attribute_value(match)))
    return None


def _parse_attribute_value(match: re.Match[str]) -> str:
    raw = next(match[key] for key in ("double", "single", "bare") if match[key] is not None)
    return html.unescape(K_MARKDOWN_ATTRIBUTE_ESCAPE.sub(r"\1", raw.replace("\n", " ")))


__all__ = ["parse_attributes", "MarkdownAttributes"]
