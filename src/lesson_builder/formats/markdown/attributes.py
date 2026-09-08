"""Entry points: `validate_attributes`, `normalize_lang`, and `normalize_text` handle Pandoc attributes.

The attr registry normalizes ``data-*`` keys, validates reserved keys against
their expected types/enums, warns on unknown keys (with typo suggestions from
:mod:`difflib`), and returns the cleaned attribute bag plus a list of warnings.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from dataclasses import field
from difflib import get_close_matches
from typing import Literal

K_REGISTRY_RESERVED_KEYS: frozenset[str] = frozenset(
    {"lex", "lang", "tense", "difficulty", "meaning", "role_block", "objectives"}
)

K_REGISTRY_SENTENCE_KEYS: frozenset[str] = frozenset({"tense"})

K_REGISTRY_LANG_CODES: dict[str, str] = {"nb": "no", "no": "no", "en": "en"}

K_REGISTRY_DIFFICULTY_LEVELS: frozenset[str] = frozenset({"A1", "A2", "B1", "B2", "C1", "C2"})

K_REGISTRY_DATA_PREFIX = "data-"

K_TYPO_SUGGESTION_CUTOFF = 0.6


@dataclass
class AttrRegistryResult:
    """Validated attribute bag plus warnings collected during validation.

    ``metadata`` holds all recognized and unknown keys (minus ``lang`` which is
    extracted into its own field). ``lang`` is the normalized language code or
    ``None`` when absent. ``warnings`` holds one human-readable string per
    unknown key.
    """

    metadata: dict[str, str] = field(default_factory=dict)
    lang: Literal["no", "en"] | None = None
    sentence_keys: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def normalize_lang(raw_lang: str) -> Literal["no", "en"]:
    """Normalize a source language code (``nb`` -> ``no``). Raise on unknown."""
    normalized = K_REGISTRY_LANG_CODES.get(raw_lang)
    if normalized is None:
        raise ValueError(
            f"unsupported language code {raw_lang!r} (expected one of: {', '.join(sorted(K_REGISTRY_LANG_CODES))})"
        )
    return normalized  # type: ignore[return-value]


def validate_attributes(raw_attrs: dict[str, str], snippet: str) -> AttrRegistryResult:
    """Validate a pandoc attribute dict against the reserved-key registry.

    * Strips ``data-`` prefixes.
    * Extracts and normalizes ``lang``.
    * Validates ``difficulty`` against CEFR levels.
    * Moves sentence-level keys (``tense``) into ``sentence_keys``.
    * Warns on unknown keys with a typo suggestion, preserving them in metadata.
    """
    result = AttrRegistryResult()

    for raw_key, raw_value in raw_attrs.items():
        key = _strip_data_prefix(raw_key)
        value = _clean_attr_value(raw_value)

        if key == "lang":
            result.lang = normalize_lang(value)
            continue

        if key in K_REGISTRY_SENTENCE_KEYS:
            result.sentence_keys[key] = value
            continue

        if key == "difficulty":
            _validate_difficulty(value, snippet)
            result.metadata[key] = value
            continue

        if key in K_REGISTRY_RESERVED_KEYS:
            result.metadata[key] = value
            continue

        suggestion = _typo_suggestion(key)
        warning = f"Unknown attribute {key!r} in {snippet}"
        if suggestion:
            warning += f" (did you mean {suggestion!r}?)"
        warning += " — preserved in metadata."
        result.warnings.append(warning)
        result.metadata[key] = value

    return result


def normalize_text(text: str) -> str:
    """NFC-normalize a text value."""
    return unicodedata.normalize("NFC", text)


def _strip_data_prefix(key: str) -> str:
    """Strip the ``data-`` prefix from an attribute key."""
    if key.startswith(K_REGISTRY_DATA_PREFIX):
        return key[len(K_REGISTRY_DATA_PREFIX) :]
    return key


def _clean_attr_value(value: str) -> str:
    """Clean and NFC-normalize an attribute value."""
    return normalize_text(value.strip())


def _validate_difficulty(value: str, snippet: str) -> None:
    """Raise if a difficulty value is not a recognized CEFR level."""
    if value not in K_REGISTRY_DIFFICULTY_LEVELS:
        raise ValueError(
            f"Invalid difficulty {value!r} in {snippet}. "
            f"Expected one of: {', '.join(sorted(K_REGISTRY_DIFFICULTY_LEVELS))}."
        )


def _typo_suggestion(key: str) -> str | None:
    """Return the closest reserved-key match for an unknown key, or ``None``."""
    matches = get_close_matches(
        key,
        sorted(K_REGISTRY_RESERVED_KEYS),
        n=1,
        cutoff=K_TYPO_SUGGESTION_CUTOFF,
    )
    return matches[0] if matches else None
