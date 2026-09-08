"""Not a check itself — typed Portable-Text content-block contracts."""

from __future__ import annotations

from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from lesson_builder.domain.lesson.models.inline import InlineSpan

Spans = list[InlineSpan]

# The authored provider-neutral vocal-presentation vocabulary. A missing
# profile means unconstrained; the value is never inferred from names or text.
K_READING_VOICE_PROFILES: frozenset[str] = frozenset({"feminine", "masculine"})


class ReadingBlock(BaseModel):
    """One learner-language reading unit, optionally belonging to a dialogue."""

    kind: Literal["reading"]
    spans: Spans
    translation: str = Field(min_length=1)
    speaker_id: str | None = None
    speaker_name: str | None = None
    dialogue_id: str | None = None
    character_id: str | None = None
    voice_profile: str | None = None

    @model_validator(mode="after")
    def _dialogue_metadata_is_complete(self) -> ReadingBlock:
        metadata = (self.speaker_id, self.speaker_name, self.dialogue_id)
        if any(value is not None for value in metadata) and not all(value and value.strip() for value in metadata):
            raise ValueError("reading dialogue metadata requires speaker_id, speaker_name, and dialogue_id")
        if self.character_id is not None:
            if not self.character_id.strip():
                raise ValueError("reading character_id must be a non-empty string")
            if not all(value and value.strip() for value in metadata):
                raise ValueError("reading character_id requires speaker_id, speaker_name, and dialogue_id")
        if self.voice_profile is not None:
            if not self.voice_profile.strip():
                raise ValueError("reading voice_profile must be a non-empty string")
            if self.voice_profile not in K_READING_VOICE_PROFILES:
                raise ValueError(f"reading voice_profile must be one of {sorted(K_READING_VOICE_PROFILES)!r}")
            if not all(value and value.strip() for value in metadata):
                raise ValueError("reading voice_profile requires speaker_id, speaker_name, and dialogue_id")
        return self

    model_config = ConfigDict(extra="forbid")


class _BlockBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeadingBlock(_BlockBase):
    kind: Literal["heading"]
    level: Literal[3, 4]
    spans: Spans


class ParagraphBlock(_BlockBase):
    kind: Literal["paragraph"]
    spans: Spans


class ListBlock(_BlockBase):
    kind: Literal["list"]
    ordered: bool
    items: list[Spans]


class RuleBlock(_BlockBase):
    kind: Literal["rule"]
    statement: Spans


class ExampleItem(_BlockBase):
    no: Spans
    en: Spans


class ExampleBlock(_BlockBase):
    kind: Literal["example"]
    no: Spans
    en: Spans


class ExamplesBlock(_BlockBase):
    kind: Literal["examples"]
    items: list[ExampleItem]


class WordListItem(_BlockBase):
    term: str
    form: str


class WordListBlock(_BlockBase):
    kind: Literal["word_list"]
    items: list[WordListItem]


class TableBlock(_BlockBase):
    kind: Literal["table"]
    col_langs: list[Literal["en", "no"]]
    headers: list[Spans]
    rows: list[list[Spans]]

    @model_validator(mode="after")
    def _columns_consistent(self) -> TableBlock:
        n = len(self.col_langs)
        if len(self.headers) != n:
            raise ValueError(f"col_langs ({n}) must match header count ({len(self.headers)})")
        for i, row in enumerate(self.rows):
            if len(row) != n:
                raise ValueError(f"col_langs ({n}) must match row {i} width ({len(row)})")
        return self


class CalloutBlock(_BlockBase):
    kind: Literal["callout"]
    level: Literal["tip", "warning", "note"]
    blocks: list[Block]


Block = Annotated[
    HeadingBlock
    | ParagraphBlock
    | ReadingBlock
    | ListBlock
    | RuleBlock
    | ExampleBlock
    | ExamplesBlock
    | WordListBlock
    | TableBlock
    | CalloutBlock,
    Field(discriminator="kind"),
]

CalloutBlock.model_rebuild()
