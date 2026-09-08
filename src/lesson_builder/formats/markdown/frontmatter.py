"""Entry points: `extract_frontmatter`, `parse_frontmatter`, and `parse_source_frontmatter`."""

from __future__ import annotations

import re
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from lesson_builder.formats.yaml import load_unique_yaml

K_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
BloomLevel = Literal["remember", "understand", "apply", "analyze"]


class FrontmatterObjective(BaseModel):
    """One objective entry in the authored Markdown front matter."""

    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str
    bloom_targets: list[BloomLevel] = Field(min_length=1)


class LessonFrontmatter(BaseModel):
    """Typed front matter, including fields that exist only in lesson.md."""

    model_config = ConfigDict(extra="forbid")

    type: str
    slug: str
    title: str
    cefr_level: str
    goal: str
    default_lang: str
    grounding_mode: str
    bloom_targets: list[BloomLevel] = Field(min_length=1)
    objectives: list[FrontmatterObjective] = Field(min_length=1)
    requirements_ref: str


def extract_frontmatter(markdown_text: str) -> str:
    """Return YAML front matter without interpreting the document body."""
    if not markdown_text.startswith("---\n"):
        return ""
    end = markdown_text.find("\n---", 4)
    return markdown_text[4:end] if end >= 0 else ""


def parse_source_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Read raw source front matter and return its mapping and exact suffix."""
    if not text.startswith("---"):
        raise ValueError("source file must start with YAML front matter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("source file has an unterminated YAML front matter block")
    value = yaml.safe_load(text[4:end])
    if not isinstance(value, dict):
        raise TypeError("source front matter must be a mapping")
    return value, text[end + 4 :]


def parse_frontmatter(md_text: str) -> tuple[LessonFrontmatter, str]:
    """Split ``md_text`` into validated front matter and the Markdown body."""
    match = K_FRONTMATTER_RE.match(md_text)
    if not match:
        raise ValueError("No YAML frontmatter found — lesson.md must start with a '---' block.")

    yaml_text = match.group(1)
    body = md_text[match.end() :]
    try:
        raw = load_unique_yaml(yaml_text)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(raw, dict):
        raise TypeError("Frontmatter must be a YAML mapping (key: value pairs).")
    try:
        frontmatter = LessonFrontmatter.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Frontmatter validation failed: {_format_validation_error(exc)}") from exc
    return frontmatter, body


def _format_validation_error(exc: ValidationError) -> str:
    """Format a front-matter validation error for an author."""
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        lines.append(f"{loc}: {error['msg']}")
    return "; ".join(lines)


__all__ = [
    "FrontmatterObjective",
    "LessonFrontmatter",
    "extract_frontmatter",
    "parse_frontmatter",
    "parse_source_frontmatter",
]
