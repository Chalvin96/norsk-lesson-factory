"""Entry points: `split_lesson_source`, `split_exercise_source`, `emit_block_fields` for draft editing, `visible_source_text` for chat excerpts, and `summary_source_text` for approval summaries."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

import yaml

from lesson_builder.formats.markdown import panflute
from lesson_builder.formats.markdown.pandoc import parse_markdown
from lesson_builder.formats.settings import K_FORMATS_ATTRIBUTE
from lesson_builder.formats.settings import K_FORMATS_EXAMPLE_ROW
from lesson_builder.formats.settings import K_FORMATS_EXERCISE
from lesson_builder.formats.settings import K_FORMATS_LEGACY_LABEL
from lesson_builder.formats.settings import K_FORMATS_SECTION
from lesson_builder.formats.settings import K_FORMATS_VISIBLE_EMPHASIS_MARKER
from lesson_builder.formats.settings import K_FORMATS_VISIBLE_HEADING_ATTRIBUTES
from lesson_builder.formats.settings import K_FORMATS_VISIBLE_HEADING_MARKER
from lesson_builder.formats.settings import K_FORMATS_VISIBLE_LIST_MARKER


@dataclass(frozen=True)
class SourceBlock:
    """A source span with its row identity and editable field projection."""

    key: str
    kind: str
    source: str
    start: int
    end: int
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LessonSource:
    """Frontmatter and ordered source spans, including exact separators."""

    frontmatter: str
    blocks: tuple[SourceBlock, ...]


def split_lesson_source(text: str) -> LessonSource:
    """Split authored Markdown without changing any bytes, following the block-editor prototype."""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks: list[SourceBlock] = []
    section = ""
    block_index = 0
    index = 0
    while index < len(lines) and not lines[index].startswith("## "):
        index += 1
    frontmatter = text[: offsets[index]]
    while index < len(lines):
        start = index
        line = lines[index].rstrip("\r\n")
        heading = K_FORMATS_SECTION.match(line)
        exercise = K_FORMATS_EXERCISE.match(line.strip())
        if line.startswith("## "):
            section = heading.group(2) if heading else f"section-{index + 1}"
            block_index = 0
            kind = "heading"
            key = section
            index += 1
        elif exercise:
            kind = "exercise"
            key = exercise.group(1)
            index += 1
            section = "unrendered"
        else:
            key = f"{section}#{block_index}"
            block_index += 1
            kind, index = _consume_block(lines, index)
        while index < len(lines) and not lines[index].strip():
            index += 1
        source = text[offsets[start] : offsets[index]]
        blocks.append(SourceBlock(key, kind, source, offsets[start], offsets[index], _read_fields(kind, source)))
    return LessonSource(frontmatter, tuple(blocks))


def split_exercise_source(text: str) -> tuple[SourceBlock, ...]:
    """Find YAML item spans by handle/id; keep other items and comments byte-identical."""
    try:
        document = yaml.compose(text, Loader=yaml.CSafeLoader)
    except yaml.YAMLError:
        return ()
    if not isinstance(document, yaml.SequenceNode):
        return ()
    blocks: list[SourceBlock] = []
    for index, item in enumerate(document.value):
        if not isinstance(item, yaml.MappingNode):
            continue
        identity = {key.value: value.value for key, value in item.value if isinstance(value, yaml.ScalarNode)}
        key = identity.get("id", identity.get("handle"))
        if not key:
            continue
        start, end = _exercise_span(text, document, index)
        blocks.append(SourceBlock(key, "exercise", text[start:end], start, end))
    return tuple(blocks)


def emit_block_fields(block: SourceBlock, fields: dict[str, str]) -> str:
    """Patch supported form fields while preserving unrelated attributes and Markdown."""
    if not block.fields or set(fields) - set(block.fields):
        raise ValueError("unsupported block fields; use Source")
    values = block.fields | fields
    lines = block.source.rstrip("\r\n").splitlines()
    if block.kind == "heading":
        match = K_FORMATS_SECTION.match(lines[0])
        if match is None:
            raise ValueError("heading has no source identity")
        lines[0] = lines[0][: match.start(1)] + values["title"] + lines[0][match.end(1) :]
    elif block.kind == "paragraph":
        return values["text"]
    elif block.kind == "reading":
        for name in ("speaker_name", "translation"):
            if name in fields:
                lines[0] = _patch_attribute(lines[0], name, values[name])
        lines[1:-1] = values["body"].splitlines()
    elif block.kind == "rule":
        lines[1:-1] = values["statement"].splitlines()
    elif block.kind in ("example", "examples"):
        lines = _emit_examples(lines, block.kind, values)
    return "\n".join(lines)


def replace_source_span(text: str, block: SourceBlock, replacement: str) -> str:
    """Replace a span's content, retaining its original inter-block separator."""
    gap = block.source[len(block.source.rstrip("\r\n")) :]
    normalized = normalize_source(replacement).rstrip("\n")
    return text[: block.start] + normalized + gap + text[block.end :]


def normalize_source(text: str) -> str:
    """Use LF, strip trailing horizontal whitespace, and end with one newline."""
    return (
        "\n".join(line.rstrip(" \t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).rstrip(
            "\n"
        )
        + "\n"
    )


def visible_source_text(source: str) -> str:
    """Collapse a source span for the existing chat excerpt contract."""
    visible: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(":::"):
            continue
        if stripped.startswith("#"):
            stripped = K_FORMATS_VISIBLE_HEADING_ATTRIBUTES.sub("", K_FORMATS_VISIBLE_HEADING_MARKER.sub("", stripped))
        else:
            stripped = K_FORMATS_VISIBLE_LIST_MARKER.sub("", stripped)
        visible.append(K_FORMATS_VISIBLE_EMPHASIS_MARKER.sub("", stripped))
    return " ".join(visible)


def summary_source_text(source: str) -> str:
    """Project parsed Markdown wording and reading metadata for approval summaries."""
    document = parse_markdown(source)
    visible: list[str] = []
    for block in document.content:
        reading = block if isinstance(block, panflute.Div) and "reading" in block.classes else None
        if reading and reading.attributes.get("speaker_name"):
            visible.append(reading.attributes["speaker_name"])
        text = panflute.stringify(block).strip()
        if text:
            visible.append(text)
        if reading and reading.attributes.get("translation"):
            translation = panflute.stringify(parse_markdown(reading.attributes["translation"])).strip()
            if translation:
                visible.append(translation)
    return " ".join(visible)


def _consume_block(lines: list[str], index: int) -> tuple[str, int]:
    line = lines[index].rstrip("\r\n")
    if K_FORMATS_LEGACY_LABEL.fullmatch(line.strip(" *\t\r\n")):
        following = index + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        if following < len(lines) and lines[following].startswith(("::: examples", "::: example")):
            return _consume_fence(lines, following)
    if line.startswith(":::") and line.strip() != ":::":
        return _consume_fence(lines, index)
    if line.startswith("### "):
        return "subheading", index + 1
    kind = "list" if line.startswith(("- ", "1. ")) else "paragraph"
    index += 1
    while index < len(lines) and lines[index].strip() and not lines[index].startswith((":::", "{{")):
        index += 1
    return kind, index


def _exercise_span(text: str, document: yaml.SequenceNode, index: int) -> tuple[int, int]:
    item = document.value[index]
    if document.flow_style:
        return item.start_mark.index, item.end_mark.index
    start = text.rfind("\n", 0, item.start_mark.index) + 1
    end = (
        len(text)
        if index + 1 == len(document.value)
        else text.rfind("\n", 0, document.value[index + 1].start_mark.index) + 1
    )
    return start, end


def _consume_fence(lines: list[str], index: int) -> tuple[str, int]:
    kind = _fence_kind(lines[index][3:].strip())
    index += 1
    depth = 1
    while index < len(lines):
        current = lines[index].strip()
        if current == ":::" and depth == 1:
            return kind, index + 1
        if lines[index].startswith("## "):
            return "error", index
        if current.startswith(":::"):
            depth += -1 if current == ":::" else 1
        index += 1
    return "error", index


def _emit_examples(lines: list[str], kind: str, values: dict[str, str]) -> list[str]:
    counters = {"no": 0, "en": 0}
    for index, line in enumerate(lines):
        row = K_FORMATS_EXAMPLE_ROW.match(line)
        if row:
            language = row.group(2)
            name = language if kind == "example" else f"{language}_{counters[language]}"
            lines[index] = row.group(1) + values[name]
            counters[language] += 1
    return lines


def _fence_kind(spec: str) -> str:
    for kind in ("reading", "examples", "example", "rule", "callout", "word_list", "table"):
        if spec == kind or spec.startswith((f"{kind} ", f"{{.{kind} ", f"{{.{kind}}}")):
            return kind
    return "raw"


def _read_fields(kind: str, source: str) -> dict[str, str]:
    lines = source.rstrip("\r\n").splitlines()
    if not lines:
        return {}
    if kind == "heading":
        match = K_FORMATS_SECTION.match(lines[0])
        return {"title": match.group(1)} if match else {}
    if kind == "paragraph":
        return {"text": "\n".join(lines)}
    if kind == "reading":
        return _reading_fields(lines)
    if kind == "rule":
        return {"statement": "\n".join(lines[1:-1])}
    if kind in ("example", "examples"):
        return _example_fields(kind, lines)
    return {}


def _reading_fields(lines: list[str]) -> dict[str, str]:
    attrs = {
        match.group(1): next(value for value in match.groups()[1:] if value is not None)
        for match in K_FORMATS_ATTRIBUTE.finditer(lines[0])
    }
    return {
        "speaker_name": attrs.get("speaker_name", ""),
        "translation": attrs.get("translation", ""),
        "body": "\n".join(lines[1:-1]),
    }


def _example_fields(kind: str, lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    counters = {"no": 0, "en": 0}
    for line in lines[1:-1]:
        match = K_FORMATS_EXAMPLE_ROW.match(line)
        if match:
            language = match.group(2)
            key = language if kind == "example" else f"{language}_{counters[language]}"
            result[key] = match.group(3)
            counters[language] += 1
    return result


def _patch_attribute(line: str, name: str, value: str) -> str:
    if "\n" in value or "\r" in value or ('"' in value and "'" in value):
        raise ValueError("attribute must fit on one line and use an available quote delimiter; use Source")
    for match in K_FORMATS_ATTRIBUTE.finditer(line):
        if match.group(1) == name:
            quote = _attribute_quote(match.group(2), match.group(3), value)
            return line[: match.start()] + f"{name}={quote}{value}{quote}" + line[match.end() :]
    quote = "'" if '"' in value else '"'
    return line.rstrip("}") + f" {name}={quote}{value}{quote}}}"


def _attribute_quote(double_quoted: str | None, single_quoted: str | None, value: str) -> str:
    if double_quoted is not None and '"' not in value:
        return '"'
    if single_quoted is not None and "'" not in value:
        return "'"
    return "'" if '"' in value else '"'
