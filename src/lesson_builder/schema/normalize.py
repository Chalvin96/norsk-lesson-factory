"""Text normalization for `build` grading: NFC + whitespace (case/punctuation significant)."""

from __future__ import annotations

import re
import unicodedata


def normalize_for_compare(text: str) -> str:
    nfc = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", nfc.strip())
