"""Entry points: `rewrite_yaml_scalars`, `rewrite_yaml_sequence_items`, and `insert_yaml_sequence_item`."""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode
from ruamel.yaml.nodes import Node
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.nodes import SequenceNode

from lesson_builder.formats.settings import K_FORMATS_WORKBENCH_DOCUMENT_WIDTH
from lesson_builder.formats.settings import K_FORMATS_WORKBENCH_SCALAR_WIDTH


def rewrite_yaml_scalars(text: str, updates: dict[tuple[str | int, ...], str | int]) -> str:
    """Round-trip edited scalars while retaining every byte outside their source spans."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = K_FORMATS_WORKBENCH_SCALAR_WIDTH
    document = yaml.load(text)
    nodes = yaml.compose(text)
    replacements: list[tuple[int, int, str]] = []
    for path, value in updates.items():
        owner = document
        node = nodes
        for key in path[:-1]:
            owner = owner[key]
            node = _child(node, key)
        key = path[-1]
        if owner[key] == value:
            continue
        owner[key] = value
        original = _child(node, key)
        if not isinstance(original, ScalarNode):
            raise TypeError("curriculum edit requires a scalar")
        rendered = StringIO()
        yaml.dump({"value": owner[key]}, rendered)
        emitted = _child(yaml.compose(rendered.getvalue()), "value")
        replacement = rendered.getvalue()[emitted.start_mark.index : emitted.end_mark.index]
        if isinstance(value, str) and (original.style in ("|", ">") or "\n" in value):
            replacement = json.dumps(value, ensure_ascii=False)
        original_text = text[original.start_mark.index : original.end_mark.index]
        if original_text.endswith("\n"):
            replacement = replacement.rstrip("\n") + ("\r\n" if original_text.endswith("\r\n") else "\n")
        replacements.append((original.start_mark.index, original.end_mark.index, replacement))
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    yaml.load(text)
    return text


def insert_yaml_sequence_item(text: str, key: str, item: dict[str, Any], position: int) -> str:
    """Insert a serialized record without emitting any existing document bytes."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = K_FORMATS_WORKBENCH_DOCUMENT_WIDTH
    sequence = _child(yaml.compose(text), key)
    if not isinstance(sequence, SequenceNode) or sequence.flow_style:
        raise ValueError("curriculum insertion requires a block sequence")
    if not 0 <= position <= len(sequence.value):
        raise ValueError("invalid curriculum insertion position")
    node = sequence.value[position] if position < len(sequence.value) else sequence
    offset = node.start_mark.index if position < len(sequence.value) else sequence.end_mark.index
    offset = text.rfind("\n", 0, offset) + 1 if position < len(sequence.value) else offset
    output = StringIO()
    yaml.dump([item], output)
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = " " * sequence.start_mark.column
    inserted = newline.join(indent + line for line in output.getvalue().splitlines()) + newline
    if offset == len(text) and text and not text.endswith("\n"):
        inserted = newline + inserted
    result = text[:offset] + inserted + text[offset:]
    yaml.load(result)
    return result


def rewrite_yaml_sequence_items(
    text: str,
    key: str,
    items: list[dict[str, Any]],
    identity_key: str,
) -> str:
    """Replace a block sequence, reusing unchanged item source and rewriting only changed slots."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = K_FORMATS_WORKBENCH_SCALAR_WIDTH
    document = yaml.load(text)
    nodes = yaml.compose(text)
    sequence_node = _child(nodes, key)
    if not isinstance(sequence_node, SequenceNode) or (sequence_node.flow_style and sequence_node.value):
        raise ValueError("curriculum rewrite requires a block sequence")
    if sequence_node.flow_style:
        if not items:
            return text
        return _rewrite_empty_flow_yaml_sequence(text, key, nodes, sequence_node, items, yaml)
    if not items:
        return _rewrite_empty_yaml_sequence(text, key, nodes, sequence_node, yaml)
    original_by_identity = _index_original_sequence_items(text, document[key], sequence_node, identity_key)
    rendered_items = [
        _render_yaml_sequence_item_from_source(item, original_by_identity, yaml, text, identity_key) for item in items
    ]
    replacement = "".join(rendered_items)
    start = sequence_node.start_mark.index
    end = sequence_node.end_mark.index
    result = text[:start] + replacement + text[end:]
    yaml.load(result)
    return result


def _index_original_sequence_items(
    text: str, original_items: list[Any], sequence_node: SequenceNode, identity_key: str
) -> dict[str, tuple[dict[str, Any], str]]:
    original_nodes = sequence_node.value
    original_by_identity: dict[str, tuple[dict[str, Any], str]] = {}
    for index, item in enumerate(original_items):
        identity = item[identity_key]
        node = original_nodes[index]
        start = _line_start(text, node.start_mark.index)
        end = (
            _line_start(text, original_nodes[index + 1].start_mark.index)
            if index + 1 < len(original_nodes)
            else sequence_node.end_mark.index
        )
        original_by_identity[identity] = (dict(item), text[start:end])
    return original_by_identity


def _render_yaml_sequence_item_from_source(
    item: dict[str, Any],
    original_by_identity: dict[str, tuple[dict[str, Any], str]],
    yaml: YAML,
    text: str,
    identity_key: str,
) -> str:
    original = original_by_identity.get(item[identity_key])
    if original is None:
        return _render_yaml_sequence_item(item, yaml, text)
    old_values, source = original
    if old_values == item:
        return source
    scalar_updates: dict[tuple[str | int, ...], str | int] = {
        (0, field): value
        for field, value in item.items()
        if isinstance(value, str | int) and old_values.get(field) != value
    }
    changed_fields = {field for field in item if old_values.get(field) != item[field]}
    if changed_fields != {path[1] for path in scalar_updates}:
        return _render_yaml_sequence_item(item, yaml, text)
    if scalar_updates:
        source = rewrite_yaml_scalars(source, scalar_updates)
    return source


def _child(node: Node, key: str | int) -> Node:
    if isinstance(node, MappingNode):
        child = next(value for name, value in node.value if name.value == key)
        if isinstance(child, Node):
            return child
    if isinstance(node, SequenceNode) and isinstance(key, int):
        child = node.value[key]
        if isinstance(child, Node):
            return child
    raise ValueError("invalid YAML field path")


def _rewrite_empty_yaml_sequence(text: str, key: str, nodes: Node, sequence: SequenceNode, yaml: YAML) -> str:
    if not isinstance(nodes, MappingNode):
        raise TypeError("curriculum rewrite requires a mapping document")
    key_node = next(name for name, value in nodes.value if value is sequence and name.value == key)
    colon = text.find(":", key_node.end_mark.index, sequence.start_mark.index)
    if colon < 0:
        raise ValueError("curriculum sequence has no mapping key")
    separator = text[colon + 1 : sequence.start_mark.index]
    line_end = separator.find("\n")
    inline = separator if line_end < 0 else separator[:line_end]
    comment = inline.strip() if inline.lstrip().startswith("#") else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    replacement = ": []" + (f" {comment}" if comment else "") + newline
    result = text[:colon] + replacement + text[sequence.end_mark.index :]
    yaml.load(result)
    return result


def _rewrite_empty_flow_yaml_sequence(
    text: str,
    key: str,
    nodes: Node,
    sequence: SequenceNode,
    items: list[dict[str, Any]],
    yaml: YAML,
) -> str:
    if not isinstance(nodes, MappingNode):
        raise TypeError("curriculum rewrite requires a mapping document")
    key_node = next(name for name, value in nodes.value if value is sequence and name.value == key)
    colon = text.find(":", key_node.end_mark.index, sequence.start_mark.index)
    if colon < 0:
        raise ValueError("curriculum sequence has no mapping key")
    line_end = text.find("\n", sequence.end_mark.index)
    if line_end < 0:
        line_end = len(text)
        suffix_start = line_end
    else:
        suffix_start = line_end + 1
    trailing = text[sequence.end_mark.index : line_end].strip()
    comment = trailing if trailing.startswith("#") else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    output = StringIO()
    yaml.dump(items, output)
    indent = " " * key_node.start_mark.column
    block = "".join(indent + line + newline for line in output.getvalue().splitlines())
    header = text[: colon + 1] + (f" {comment}" if comment else "") + newline
    result = header + block + text[suffix_start:]
    yaml.load(result)
    return result


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _render_yaml_sequence_item(item: dict[str, Any], yaml: YAML, source: str) -> str:
    output = StringIO()
    yaml.dump([item], output)
    newline = "\r\n" if "\r\n" in source else "\n"
    return output.getvalue().replace("\n", newline)
