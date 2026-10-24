"""Entry point: `create_markdown_reader` installs the supported Pandoc-compatible syntax rules."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin

from lesson_builder.formats.markdown.markdown_attributes import MarkdownAttributes
from lesson_builder.formats.markdown.markdown_attributes import parse_attributes


class _MarkdownReader(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        """Recognize every destination so the prose mapper rejects all links and images."""
        return True


def create_markdown_reader() -> MarkdownIt:
    """Create the reusable parser; each parse owns its token and environment state."""
    reader = _MarkdownReader("commonmark", {"html": True}).enable(["table", "strikethrough"])
    reader.use(deflist_plugin)
    reader.use(footnote_plugin)
    reader.use(container_plugin, name="div", marker=":")
    reader.block.ruler.at("container_div", _div_rule, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    reader.inline.ruler.before("link", "bracketed_span", _bracketed_span_rule)
    reader.inline.ruler.before("escape", "nonbreaking_space", _nonbreaking_space_rule)
    for ruler in (reader.core.ruler, reader.block.ruler, reader.inline.ruler, reader.inline.ruler2):
        ruler.getRules("")
    return reader


def _bracketed_span_rule(state: StateInline, silent: bool) -> bool:
    if state.src[state.pos] != "[":
        return False
    end = state.md.helpers.parseLinkLabel(state, state.pos, False)
    if end < 0:
        return False
    parsed = parse_attributes(state.src, end + 1)
    if parsed is None:
        return False
    attributes, after = parsed
    if not silent:
        token = state.push("span_inline", "span", 0)
        token.meta["pandoc_attributes"] = attributes
        token.children = []
        state.md.inline.parse(state.src[state.pos + 1 : end], state.md, state.env, token.children)
    state.pos = after
    return True


def _nonbreaking_space_rule(state: StateInline, silent: bool) -> bool:
    if state.src[state.pos : state.pos + 2] != "\\ ":
        return False
    if not silent:
        state.pending += " "
    state.pos += 2
    return True


def _get_line(state: StateBlock, index: int) -> str:
    return state.src[state.bMarks[index] + state.tShift[index] : state.eMarks[index]]


def _find_div_attributes(state: StateBlock, line: int) -> MarkdownAttributes | None:
    text = _get_line(state, line)
    if not text.startswith(":::"):
        return None
    info = text.lstrip(":").strip()
    if re.fullmatch(r"[\w-]+", info):
        return MarkdownAttributes("", [info], [])
    parsed = parse_attributes(info)
    return parsed[0] if parsed is not None and not info[parsed[1] :].strip() else None


def _div_rule(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    if state.is_code_block(start):
        return False
    attributes = _find_div_attributes(state, start)
    if attributes is None:
        return False
    if silent:
        return True
    depth = 1
    closing = start + 1
    while closing < end:
        line = _get_line(state, closing)
        if line.startswith(":::"):
            if not line.strip(": \t"):
                depth -= 1
                if depth == 0:
                    break
            elif _find_div_attributes(state, closing) is not None:
                depth += 1
        closing += 1
    token = state.push("container_div_open", "div", 1)
    token.meta["pandoc_attributes"] = attributes
    old_parent, old_max = state.parentType, state.lineMax
    state.parentType, state.lineMax = "container", closing
    try:
        state.md.block.tokenize(state, start + 1, closing)
    finally:
        state.parentType, state.lineMax = old_parent, old_max
    state.push("container_div_close", "div", -1)
    state.line = min(closing + 1, end)
    return True


__all__ = ["create_markdown_reader"]
