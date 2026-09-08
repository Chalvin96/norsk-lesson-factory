"""Entry point: `build_authoring_prose_lines` prepares Markdown lines for source policy."""

from __future__ import annotations

import re


def build_authoring_prose_lines(text: str) -> tuple[list[str], list[str], set[int]]:
    """Return raw, undecorated, and linguistic-example line indexes."""
    lines = text.splitlines()
    plain_lines = [_build_plain_request_line(line) for line in lines]
    return lines, plain_lines, _collect_linguistic_div_content_indexes(lines)


def _build_plain_request_line(line: str) -> str:
    """Strip Markdown decoration that may wrap an internal request label."""
    plain_line = re.sub(r"^#{1,6}\s+", "", line.strip())
    plain_line = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", plain_line)
    return plain_line.replace("**", "").strip()


def _collect_linguistic_div_content_indexes(lines: list[str]) -> set[int]:
    """Return line indexes contained by example or reading Pandoc divs."""
    content_indexes: set[int] = set()
    div_stack: list[bool] = []
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        div_start = re.match(r"^:{3,}\s+(?P<descriptor>\S.*)$", stripped)
        if div_start is not None:
            descriptor = div_start.group("descriptor")
            is_linguistic_div = (
                descriptor == "examples" or re.match(r"^\{\s*\.reading(?:\s|\})", descriptor) is not None
            )
            div_stack.append(is_linguistic_div)
            if is_linguistic_div:
                content_indexes.add(line_index)
            continue
        if div_stack and re.fullmatch(r":{3,}", stripped):
            div_stack.pop()
            continue
        if any(div_stack):
            content_indexes.add(line_index)
    return content_indexes


__all__ = ["build_authoring_prose_lines"]
