"""Entry point: `collect_non_norwegian_audio_words` checks authored audio text."""

from __future__ import annotations

import re

K_AUDIO_LANGUAGE_WORD_RE = re.compile(r"[A-Za-zÆØÅæøå]+")
# These are unmistakable English words that should never reach the Norwegian
# model-audio transcript. This is a narrow contamination guard, not a general
# language detector; authored spans still carry the authoritative ``lang`` tag.
# Norwegian homographs such as ``and`` (duck) and ``arrangement`` (event) must
# stay out of this list so valid Bokmål is never rejected as English.
K_AUDIO_LANGUAGE_NON_NORWEGIAN_WORDS = frozenset(
    {
        "answer",
        "are",
        "can",
        "assessment",
        "available",
        "based",
        "books",
        "commitment",
        "could",
        "does",
        "english",
        "from",
        "goodbye",
        "hello",
        "have",
        "has",
        "help",
        "how",
        "in",
        "morning",
        "meaning",
        "my",
        "need",
        "not",
        "of",
        "on",
        "or",
        "please",
        "personal",
        "question",
        "report",
        "sentence",
        "speak",
        "thanks",
        "thank",
        "that",
        "the",
        "this",
        "there",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "one",
        "two",
        "want",
        "expected",
        "fixed",
        "result",
        "information",
        "item",
        "label",
        "what",
        "where",
        "why",
        "will",
        "with",
        "would",
        "you",
        "target",
    }
)


def collect_non_norwegian_audio_words(text: str) -> list[str]:
    """Return sorted known non-Norwegian words in one prospective audio text."""
    words = K_AUDIO_LANGUAGE_WORD_RE.findall(text.lower())
    return sorted({word for word in words if word in K_AUDIO_LANGUAGE_NON_NORWEGIAN_WORDS})


__all__ = ["collect_non_norwegian_audio_words"]
