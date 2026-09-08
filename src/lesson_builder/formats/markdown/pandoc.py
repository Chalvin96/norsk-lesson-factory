"""Entry point: `parse_markdown` converts markdown text into a panflute Doc (called by the lesson compile service).

Invokes pypandoc with the pinned reader string
``markdown+native_spans+bracketed_spans-smart`` and loads the resulting JSON
into a panflute ``Doc`` via :func:`panflute.load`. The parser, fingerprint, and
doc helper interface are consumed by the source package and the catalog
authoring runtime.
"""

from __future__ import annotations

import io
import json
from typing import Any

import panflute
import pypandoc

K_PANDOC_READER = "markdown+native_spans+bracketed_spans-smart"
K_PANDOC_WRITER = "json"


def parse_markdown(md_text: str) -> panflute.Doc:
    """Parse markdown text into a panflute ``Doc`` using the pinned reader."""
    doc_json = pypandoc.convert_text(
        md_text,
        format=K_PANDOC_READER,
        to=K_PANDOC_WRITER,
    )
    return panflute.load(io.StringIO(doc_json))


def compiler_fingerprint() -> dict[str, str]:
    """Return the compiler fingerprint (pandoc/panflute versions + reader)."""
    return {
        "pandoc": pypandoc.get_pandoc_version(),
        "panflute": panflute.__version__,
        "reader": K_PANDOC_READER,
    }


def doc_meta(doc: panflute.Doc) -> dict[str, Any]:
    """Return the metadata dict from a panflute ``Doc`` (parsed JSON form)."""
    raw: dict[str, Any] = json.loads(doc.to_json())
    meta = raw.get("meta", {})
    return dict(meta) if isinstance(meta, dict) else {}


__all__ = [
    # Adapter entry points
    "parse_markdown",
    "compiler_fingerprint",
    "doc_meta",
]
