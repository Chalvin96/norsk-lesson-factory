"""Not a check itself — one learner-visibility boundary for authored blocks.

Export projection and audio-candidate derivation must agree about which
authored content is learner-visible. This module owns that single decision:
the explicit internal request anchors that start an authoring tail inside one
containing block list, and the section titles that are internal request
anchors themselves. Both the public projection and the audio transcript
derivation consult these helpers so invisible content is never exported and
never synthesized.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

K_LEARNER_VISIBILITY_REQUEST_PREFIX_WORDS: tuple[str, ...] = (
    "short",
    "cumulative",
    "practice",
    "activity",
    "checkpoint",
    "exercise",
    "retrieval",
    "transfer",
    "review",
    "final",
    "and",
)
K_LEARNER_VISIBILITY_REQUEST_ANCHOR_RE = re.compile(
    r"^(?:(?:" + "|".join(K_LEARNER_VISIBILITY_REQUEST_PREFIX_WORDS) + r")\s+)*request(?:\s+name)?\s*[:—-]",
    re.IGNORECASE,
)


def is_internal_request_anchor(text: object) -> bool:
    """Return whether one block's flattened text is an internal request anchor.

    An anchor is the word ``request`` (optionally ``request name``) preceded
    only by the closed prefix vocabulary, followed by ``:``, ``-``, or ``—``.
    Ordinary learner prose such as ``Compare the central request:`` is not an
    anchor because its leading words are outside that vocabulary.
    """
    return isinstance(text, str) and bool(K_LEARNER_VISIBILITY_REQUEST_ANCHOR_RE.match(text))


def is_internal_request_section_title(title: object) -> bool:
    """Return whether one section title is itself an internal request anchor."""
    return is_internal_request_anchor(title)


def visible_block_indexes(blocks: object) -> list[int]:
    """Return original indexes of learner-visible blocks in one containing list.

    The first internal request anchor and every block after it, within this
    same list only, are invisible. Indexes index the authored list, so audio
    binding locators stay stable regardless of what projection removes.
    """
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        return []
    visible: list[int] = []
    for index, block in enumerate(blocks):
        if is_internal_request_anchor(block_anchor_text(block)):
            return visible
        visible.append(index)
    return visible


def block_anchor_text(block: object) -> str:
    """Flatten one authored block to the text used for anchor detection."""
    if not isinstance(block, dict):
        return ""
    kind = block.get("kind")
    if kind in {"paragraph", "heading"}:
        return _spans_text(block.get("spans"))
    if kind == "rule":
        return _spans_text(block.get("statement"))
    if kind == "example":
        return _spans_text(block.get("no"))
    if kind == "examples":
        return " ".join(_spans_text(item.get("no")) for item in block.get("items", []) if isinstance(item, dict))
    if kind == "list":
        return " ".join(_spans_text(item) for item in block.get("items", []))
    if kind == "callout":
        return " ".join(block_anchor_text(child) for child in block.get("blocks", []))
    return ""


def _spans_text(spans: object) -> str:
    """Flatten compiled inline spans, descending nested sentence spans."""
    if not isinstance(spans, list):
        return ""
    pieces: list[str] = []
    for span in spans:
        if isinstance(span, dict):
            value = span.get("value")
            if isinstance(value, str):
                pieces.append(value)
            else:
                pieces.append(_spans_text(span.get("children")))
        elif isinstance(span, str):
            pieces.append(span)
    return "".join(pieces).strip()


__all__ = [
    "block_anchor_text",
    "is_internal_request_anchor",
    "is_internal_request_section_title",
    "visible_block_indexes",
]
