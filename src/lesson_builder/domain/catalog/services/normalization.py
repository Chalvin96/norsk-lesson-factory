"""Entry points: `normalize_text` and `normalize_slug` normalize catalog labels."""

from __future__ import annotations

import re
import unicodedata

K_CATALOG_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
K_CATALOG_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize a catalog label for conservative exact-match comparisons."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return K_CATALOG_SPACE_RE.sub(" ", K_CATALOG_NON_WORD_RE.sub(" ", ascii_text)).strip()


def normalize_slug(value: str) -> str:
    """Normalize a catalog label into a safe comparison key."""
    return normalize_text(value).replace(" ", "_")


__all__ = ["normalize_slug", "normalize_text"]
