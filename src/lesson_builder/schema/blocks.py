"""Portable-Text content blocks. `table` enforces column-count consistency."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lesson_builder.schema.inline import InlineSpan

Spans = list[InlineSpan]


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
