"""Entry point: `parse_markdown` reads Markdown into the panflute AST used by lesson compilation.

markdown-it-py and the local compatibility rules emit Pandoc-shaped JSON.
The existing block and inline mappers consume the loaded document unchanged;
no Pandoc executable or automatic heading identifiers are involved.
"""

from __future__ import annotations

import io
import json
from typing import Any

from lesson_builder.formats.markdown import panflute
from lesson_builder.formats.markdown.markdown_ast import convert_markdown


def parse_markdown(md_text: str) -> panflute.Doc:
    """Parse one Markdown field into a panflute document in-process."""
    return panflute.load(io.StringIO(json.dumps(convert_markdown(md_text))))


def doc_meta(doc: panflute.Doc) -> dict[str, Any]:
    """Return the metadata dict from a panflute ``Doc`` (parsed JSON form)."""
    raw: dict[str, Any] = json.loads(doc.to_json())
    meta = raw.get("meta", {})
    return dict(meta) if isinstance(meta, dict) else {}


__all__ = [
    # Adapter entry points
    "parse_markdown",
    "doc_meta",
]
