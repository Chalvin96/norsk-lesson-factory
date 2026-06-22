"""Portable-Text inline spans. Leaf `value`s are plain text — no markdown chars."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MARKDOWN_CHARS = ("*", "`", "_")
_BLANK_MARKER_RE = re.compile(r"_{3,}")


def _no_markdown(v: str) -> str:
    # Blank markers (___+) are allowed in text values (e.g. choose stems).
    stripped = _BLANK_MARKER_RE.sub("", v)
    if any(ch in stripped for ch in _MARKDOWN_CHARS):
        raise ValueError("inline value must be plain text (no * ` _)")
    return v


class TextSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text"]
    value: str
    _no_markdown = field_validator("value")(_no_markdown)


class EmphasisSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["emphasis"]
    value: str
    _no_markdown = field_validator("value")(_no_markdown)


class StrongSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["strong"]
    value: str
    _no_markdown = field_validator("value")(_no_markdown)


class CodeSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["code"]
    value: str
    _no_markdown = field_validator("value")(_no_markdown)


class ForeignTermSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["foreign_term"]
    value: str
    lang: Literal["no", "en"]
    _no_markdown = field_validator("value")(_no_markdown)


InlineSpan = Annotated[
    TextSpan | EmphasisSpan | StrongSpan | CodeSpan | ForeignTermSpan,
    Field(discriminator="kind"),
]
