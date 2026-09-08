"""Not a check itself — recurring-character contracts and model-local invariants."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

K_CHARACTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class CharacterDefinition:
    """One intentionally recurring character in the authoring registry."""

    character_id: str
    name: str
    voice_profile: str | None = None

    @staticmethod
    def validate_id(character_id: str) -> None:
        """Reject ambiguous or unsafe registry identifiers."""
        if K_CHARACTER_ID_PATTERN.fullmatch(character_id) is None:
            raise ValueError(f"invalid character_id {character_id!r}; use lowercase kebab-case")


@dataclass(frozen=True)
class CharacterRegistry:
    """Validated provider-neutral recurring-character definitions."""

    characters: Mapping[str, CharacterDefinition]


__all__ = ["CharacterDefinition", "CharacterRegistry"]
