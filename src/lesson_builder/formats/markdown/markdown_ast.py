"""Entry point: `convert_markdown` emits the Pandoc-shaped JSON consumed by the unchanged mappers."""

from __future__ import annotations

import re
from typing import Any

from markdown_it.token import Token

from lesson_builder.formats.markdown.markdown_attributes import MarkdownAttributes
from lesson_builder.formats.markdown.markdown_attributes import parse_attributes
from lesson_builder.formats.markdown.markdown_rules import create_markdown_reader

K_MARKDOWN_API_VERSION = [1, 23, 1, 1]
K_MARKDOWN_READER = create_markdown_reader()
K_MARKDOWN_EMPTY_ATTRIBUTES = ["", [], []]
K_MARKDOWN_INLINE_KINDS = {"em": "Emph", "strong": "Strong", "link": "Link", "s": "Strikeout"}
K_MARKDOWN_TABLE_ALIGNMENTS = {"left": "AlignLeft", "right": "AlignRight", "center": "AlignCenter"}


def convert_markdown(text: str) -> dict[str, Any]:
    """Parse one independent Markdown field without subprocesses or automatic heading ids."""
    blocks, _ = _build_blocks(K_MARKDOWN_READER.parse(text), 0)
    return {"pandoc-api-version": K_MARKDOWN_API_VERSION, "meta": {}, "blocks": blocks}


def _get_attributes(token: Token) -> list[object]:
    attributes = token.meta.get("pandoc_attributes")
    return attributes.to_json() if isinstance(attributes, MarkdownAttributes) else ["", [], []]


def _build_words(text: str) -> list[dict[str, Any]]:
    return [
        {"t": "Space"} if part[0] in " \t" else {"t": "Str", "c": part} for part in re.split(r"([ \t]+)", text) if part
    ]


def _append(out: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> None:
    """Coalesce text across escaped punctuation and decoded entity tokens."""
    for node in nodes:
        if out and node["t"] == out[-1]["t"] == "Str":
            out[-1]["c"] += node["c"]
        else:
            out.append(node)


def _build_inlines(tokens: list[Token], start: int = 0, closing: str = "") -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token.type == closing:
            return out, index + 1
        if token.type.endswith("_open") and token.type[:-5] in K_MARKDOWN_INLINE_KINDS:
            content, index = _build_inlines(tokens, index + 1, token.type.replace("_open", "_close"))
            out.append(_build_inline_container(token, content))
            continue
        _append(out, _build_inline_leaf(token))
        index += 1
    return out, index


def _build_inline_container(token: Token, content: list[dict[str, Any]]) -> dict[str, Any]:
    kind = K_MARKDOWN_INLINE_KINDS[token.type[:-5]]
    if kind == "Link":
        return {
            "t": kind,
            "c": [_get_attributes(token), content, [token.attrGet("href") or "", token.attrGet("title") or ""]],
        }
    return {"t": kind, "c": content}


def _build_inline_leaf(token: Token) -> list[dict[str, Any]]:
    if token.type == "footnote_ref":
        return [{"t": "Note", "c": []}]
    if token.type == "span_inline":
        content, _ = _build_inlines(token.children or [])
        return [{"t": "Span", "c": [_get_attributes(token), content]}]
    if token.type == "text":
        return _build_words(token.content)
    if token.type in ("softbreak", "hardbreak"):
        return [{"t": "SoftBreak" if token.type == "softbreak" else "LineBreak"}]
    if token.type == "code_inline":
        return [{"t": "Code", "c": [_get_attributes(token), token.content]}]
    if token.type == "html_inline":
        return [{"t": "RawInline", "c": ["html", token.content]}]
    if token.type == "image":
        children, _ = _build_inlines(token.children or [])
        return [
            {
                "t": "Image",
                "c": [_get_attributes(token), children, [token.attrGet("src") or "", token.attrGet("title") or ""]],
            }
        ]
    raise ValueError(f"Unsupported Markdown inline token {token.type}")


def _build_heading(token: Token, inline: Token) -> dict[str, Any]:
    content = inline.content
    attributes: list[object] = ["", [], []]
    for match in re.finditer(r"\{", content):
        parsed = parse_attributes(content, match.start())
        if parsed is not None and not content[parsed[1] :].strip():
            attributes = parsed[0].to_json()
            content = content[: match.start()].rstrip()
            break
    if any(child.type == "footnote_ref" for child in inline.children or []):
        children = inline.children or []
    else:
        children = K_MARKDOWN_READER.parseInline(content)[0].children or []
    inlines, _ = _build_inlines(children)
    return {"t": "Header", "c": [int(token.tag[1:]), attributes, inlines]}


def _build_blocks(tokens: list[Token], start: int, closing: str = "") -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token.type == closing:
            return out, index + 1
        if token.type == "footnote_block_open":
            index = _require_closing_index(tokens, index) + 1
            continue
        node, index = _build_block(tokens, index)
        out.append(node)
    return out, index


def _build_block(tokens: list[Token], index: int) -> tuple[dict[str, Any], int]:
    token = tokens[index]
    if token.type in ("paragraph_open", "heading_open"):
        inline = tokens[index + 1]
        content, _ = _build_inlines(inline.children or [])
        node = (
            _build_heading(token, inline)
            if token.type == "heading_open"
            else {"t": "Plain" if token.hidden else "Para", "c": content}
        )
        return node, index + 3
    if token.type in ("container_div_open", "blockquote_open"):
        content, index = _build_blocks(tokens, index + 1, token.type.replace("_open", "_close"))
        node = (
            {"t": "Div", "c": [_get_attributes(token), content]}
            if token.type == "container_div_open"
            else {"t": "BlockQuote", "c": content}
        )
        return node, index
    if token.type in ("bullet_list_open", "ordered_list_open"):
        return _build_list(tokens, index)
    if token.type == "dl_open":
        return {"t": "DefinitionList", "c": []}, _require_closing_index(tokens, index) + 1
    if token.type == "table_open":
        return _build_table(tokens, index + 1)
    return _build_block_leaf(token), index + 1


def _require_closing_index(tokens: list[Token], index: int) -> int:
    """Return the index of the close token matching the open token at ``index``."""
    opening = tokens[index]
    closing = opening.type.replace("_open", "_close")
    for candidate in range(index + 1, len(tokens)):
        if tokens[candidate].type == closing and tokens[candidate].level == opening.level:
            return candidate
    raise ValueError(f"Unbalanced Markdown token {opening.type}")


def _build_block_leaf(token: Token) -> dict[str, Any]:
    if token.type == "hr":
        return {"t": "HorizontalRule"}
    if token.type == "html_block":
        return {"t": "RawBlock", "c": ["html", token.content]}
    if token.type in ("fence", "code_block"):
        return {"t": "CodeBlock", "c": [["", token.info.split()[:1], []], token.content.rstrip("\n")]}
    raise ValueError(f"Unsupported Markdown block token {token.type}")


def _build_list(tokens: list[Token], start: int) -> tuple[dict[str, Any], int]:
    token = tokens[start]
    closing = token.type.replace("_open", "_close")
    items: list[list[dict[str, Any]]] = []
    index = start + 1
    while index < len(tokens) and tokens[index].type != closing:
        content, index = _build_blocks(tokens, index + 1, "list_item_close")
        items.append(content)
    if token.type == "bullet_list_open":
        return {"t": "BulletList", "c": items}, index + 1
    attributes = [
        int(token.attrGet("start") or 1),
        {"t": "Decimal"},
        {"t": "Period" if token.markup == "." else "OneParen"},
    ]
    return {"t": "OrderedList", "c": [attributes, items]}, index + 1


def _build_table(tokens: list[Token], start: int) -> tuple[dict[str, Any], int]:
    head: list[object] = []
    body: list[object] = []
    alignments: list[str] = []
    row: list[object] = []
    in_head = False
    index = start
    while tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type == "tr_open":
            row = []
        elif token.type == "tr_close":
            (head if in_head else body).append([K_MARKDOWN_EMPTY_ATTRIBUTES, row])
        elif token.type in ("th_open", "td_open"):
            style = str(token.attrGet("style") or "").removeprefix("text-align:")
            alignment = K_MARKDOWN_TABLE_ALIGNMENTS.get(style, "AlignDefault")
            if in_head:
                alignments.append(alignment)
            content, _ = _build_inlines(tokens[index + 1].children or [])
            row.append([K_MARKDOWN_EMPTY_ATTRIBUTES, {"t": alignment}, 1, 1, [{"t": "Plain", "c": content}]])
            index += 2
        index += 1
    columns = [[{"t": alignment}, {"t": "ColWidthDefault"}] for alignment in alignments]
    table = [
        K_MARKDOWN_EMPTY_ATTRIBUTES,
        [None, []],
        columns,
        [K_MARKDOWN_EMPTY_ATTRIBUTES, head],
        [[K_MARKDOWN_EMPTY_ATTRIBUTES, 0, [], body]],
        [K_MARKDOWN_EMPTY_ATTRIBUTES, []],
    ]
    return {"t": "Table", "c": table}, index + 1


__all__ = ["convert_markdown"]
