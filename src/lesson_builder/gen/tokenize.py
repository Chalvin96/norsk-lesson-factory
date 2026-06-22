"""Deterministic Norwegian sentence tokenizer for build/find_fix construction.
Whitespace-delimited; punctuation stays attached to its word so tokens round-trip to
the displayed sentence. No linguistic analysis -- presentation chips only."""

from __future__ import annotations

_EDGE_PUNCT = ".,!?;:»«\"'()"


def normalize_word(word: str) -> str:
    """Strip edge punctuation for content comparison (chip 'stort.' == word 'stort')."""
    return word.strip(_EDGE_PUNCT)


def tokenize_sentence(sentence: str) -> list[str]:
    return [w for w in sentence.split() if w]


def find_word_indices(tokens: list[str], word: str) -> list[int]:
    """All token indices whose normalized form equals `word` (also normalized)."""
    target = normalize_word(word)
    return [i for i, tok in enumerate(tokens) if normalize_word(tok) == target]


def find_word_index(tokens: list[str], word: str) -> int | None:
    """First matching index, or None. Prefer find_word_indices when ambiguity matters."""
    hits = find_word_indices(tokens, word)
    return hits[0] if hits else None
