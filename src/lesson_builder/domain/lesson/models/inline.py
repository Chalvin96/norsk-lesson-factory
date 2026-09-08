"""Not a check itself — typed Portable-Text inline-span contracts.

The five wire kinds (``text``/``emphasis``/``strong``/``code``/``foreign_term``)
are the complete set that reaches the exported wire. Two additional internal-only
kinds — ``annotated`` (leaf with metadata bag) and ``sentence`` (one-level
container) — are stripped by the export projection in ``export.py`` before the
dict ever reaches the wire, so the lesson schema version does not change.
"""

from __future__ import annotations

import re
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

K_MARKDOWN_CHARS = ("*", "`", "_")
K_BLANK_MARKER_RE = re.compile(r"_{3,}")


class TextSpan(BaseModel):
    """Plain text leaf span."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["text"]
    value: str

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class EmphasisSpan(BaseModel):
    """Emphasised text leaf span (renders as italic)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["emphasis"]
    value: str

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class StrongSpan(BaseModel):
    """Strong-importance text leaf span (renders as bold)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["strong"]
    value: str

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class CodeSpan(BaseModel):
    """Inline code leaf span."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["code"]
    value: str

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class ForeignTermSpan(BaseModel):
    """Foreign-term leaf span carrying a language tag (``no`` or ``en``)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["foreign_term"]
    value: str
    lang: Literal["no", "en"]

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class AnnotatedSpan(BaseModel):
    """Internal-only leaf span carrying inline word/phrase metadata.

    Stripped to ``foreign_term`` (when ``lang`` is set) or ``text`` (when
    ``lang`` is ``None``) by the export projection; ``metadata`` is dropped.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["annotated"]
    value: str
    lang: Literal["no", "en"] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def _no_markdown(cls, v: str) -> str:
        return _validate_plain_text(v)


class SentenceSpan(BaseModel):
    """Internal-only one-level container for sentence-level tags.

    ``children`` holds word/phrase spans (the five wire kinds plus
    ``annotated``). The export projection flattens a ``sentence`` to its
    recursively-stripped children inline in place.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["sentence"]
    children: list[InlineSpan]
    metadata: dict[str, str] = Field(default_factory=dict)


InlineSpan = Annotated[
    TextSpan | EmphasisSpan | StrongSpan | CodeSpan | ForeignTermSpan | AnnotatedSpan | SentenceSpan,
    Field(discriminator="kind"),
]

# Resolve the forward reference: ``SentenceSpan.children`` is annotated
# ``list[InlineSpan]`` and ``InlineSpan`` is only fully defined above.
SentenceSpan.model_rebuild()


def _validate_plain_text(v: str) -> str:
    # Blank markers (___+) are allowed in text values (e.g. choose stems).
    stripped = K_BLANK_MARKER_RE.sub("", v)
    if any(ch in stripped for ch in K_MARKDOWN_CHARS):
        raise ValueError("inline value must be plain text (no * ` _)")
    return v
