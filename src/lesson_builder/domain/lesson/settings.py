"""Not a check itself — settings for the lesson authoring context."""

from __future__ import annotations

from typing import Literal

K_EXPORT_SPEAKER_ICON_BASE_URL = "https://api.dicebear.com/10.x/open-peeps/svg"
K_LESSON_SCHEMA_VERSION: Literal["4.0"] = "4.0"
K_EXERCISES_DEFAULT_LANG = "nb"
K_TRANSCRIPT_AUDIO_LANGUAGE = "nb-NO"
# These are unmistakable English words that should never reach the Norwegian
# model-audio transcript. This is a narrow contamination guard, not a general
# language detector; authored spans still carry the authoritative ``lang`` tag.
# Norwegian homographs such as ``and`` (duck) and ``arrangement`` (event) must
# stay out of this list so valid Bokmål is never rejected as English.
K_TRANSCRIPT_NON_NORWEGIAN_AUDIO_WORDS = frozenset(
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
K_TRANSCRIPT_AUDIO_EXERCISE_OPS: tuple[str, ...] = (
    "recall_fill",
    "judge",
    "build",
    "speak",
)
