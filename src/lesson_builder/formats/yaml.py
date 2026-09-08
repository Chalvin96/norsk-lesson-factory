"""Entry points: `load_unique_yaml` parses YAML and `quote_unquoted_yaml_scalars` repairs text."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml


def load_unique_yaml(yaml_text: str) -> object:
    """Parse YAML with a safe loader that rejects duplicate mapping keys."""
    # Instantiate the SafeLoader directly so the safety boundary is explicit
    # and callers cannot accidentally substitute an unsafe loader.
    loader = UniqueKeyLoader(yaml_text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


class UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that refuses duplicate mapping keys."""


def quote_unquoted_yaml_scalars(text: str) -> str:
    """Protect text scalars containing YAML's ambiguous colon-space syntax."""
    lines = text.splitlines(keepends=True)
    repaired: list[str] = []
    block_indent: int | None = None
    text_fields = {
        "prompt_md",
        "explanation_md",
        "stem_md",
        "sentence_md",
        "judge_prompt",
        "feedback",
        "text",
        "why",
        "target",
    }
    index = 0
    while index < len(lines):
        line = lines[index]
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        indent = len(content) - len(content.lstrip(" "))
        if block_indent is not None:
            if content.strip() and indent <= block_indent:
                block_indent = None
            else:
                repaired.append(line)
                index += 1
                continue
        mapping_result = _quote_yaml_mapping_line(lines, index, content, text_fields)
        if mapping_result is not None:
            replacement, index, block_indent = mapping_result
            repaired.append(replacement)
            continue
        list_result = _quote_yaml_list_line(content, newline)
        if list_result is not None:
            repaired.append(list_result)
            index += 1
            continue
        repaired.append(line)
        index += 1
    return "".join(repaired)


def _quote_yaml_mapping_line(
    lines: list[str], index: int, content: str, text_fields: set[str]
) -> tuple[str, int, int | None] | None:
    """Quote one YAML mapping scalar when its value needs transport protection."""
    key_match = re.match(r"^(\s*(?:-\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*)(.*)$", content)
    if key_match is None:
        return None
    prefix, key, value = key_match.groups()
    stripped = value.strip()
    indent = len(content) - len(content.lstrip(" "))
    newline = lines[index][len(content) :]
    block_result = _mapping_block_result(lines[index], index, indent, stripped)
    if block_result is not None:
        return block_result
    text_result = _mapping_text_result(lines, index, indent, key, prefix, stripped, newline, text_fields)
    if text_result is not None:
        return text_result
    return _mapping_colon_result(prefix, value, newline, index)


def _mapping_block_result(line: str, index: int, indent: int, value: str) -> tuple[str, int, int | None] | None:
    """Preserve YAML blank and block-scalar mapping values."""
    if value not in {"", "|", ">", "|-", ">-", "|+", ">+"}:
        return None
    block_indent = indent if value.startswith(("|", ">")) else None
    return line, index + 1, block_indent


def _mapping_text_result(
    lines: list[str],
    index: int,
    indent: int,
    key: str,
    prefix: str,
    value: str,
    newline: str,
    text_fields: set[str],
) -> tuple[str, int, int | None] | None:
    """Quote a text mapping and consume its indented continuation lines."""
    if key not in text_fields:
        return None
    continuation: list[str] = []
    next_index = index + 1
    while next_index < len(lines):
        candidate = lines[next_index].rstrip("\r\n")
        candidate_indent = len(candidate) - len(candidate.lstrip(" "))
        if candidate.strip() and candidate_indent <= indent:
            break
        continuation.append(candidate.strip())
        next_index += 1
    joined = " ".join(part for part in [value, *continuation] if part)
    joined = _unwrap_malformed_scalar_quotes(joined)
    return f"{prefix}{json.dumps(joined, ensure_ascii=False)}{newline}", next_index, None


def _mapping_colon_result(prefix: str, value: str, newline: str, index: int) -> tuple[str, int, int | None] | None:
    """Quote a non-text mapping value containing an ambiguous colon."""
    if value.strip()[:1] in {"'", '"', "[", "{", "#"} or not re.search(r":\s", value):
        return None
    return f"{prefix}{json.dumps(value.strip(), ensure_ascii=False)}{newline}", index + 1, None


def _quote_yaml_list_line(content: str, newline: str) -> str | None:
    """Quote one YAML list scalar containing an unescaped colon."""
    list_match = re.match(r"^(\s*-\s+)(.+)$", content)
    if list_match is None:
        return None
    prefix, value = list_match.groups()
    stripped = value.strip()
    if (
        stripped[:1] not in {"'", '"', "[", "{", "#"}
        and not re.match(r"[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped)
        and re.search(r":\s", value)
    ):
        return f"{prefix}{json.dumps(stripped, ensure_ascii=False)}{newline}"
    return None


def _unwrap_malformed_scalar_quotes(value: str) -> str:
    """Remove one model-added quote layer before emitting canonical quotes."""
    stripped = value.strip()
    if stripped.startswith('"'):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped[1:].rstrip('"').replace(r"\"", '"')
        if isinstance(parsed, str):
            return parsed
    if stripped.startswith("'"):
        return stripped[1:-1].replace("''", "'") if stripped.endswith("'") else stripped[1:]
    return stripped


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    """Construct one mapping while rejecting duplicate keys."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ValueError("YAML mapping keys must be hashable") from exc
        if duplicate:
            raise ValueError(f"duplicate YAML key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


__all__ = ["UniqueKeyLoader", "load_unique_yaml", "quote_unquoted_yaml_scalars"]
