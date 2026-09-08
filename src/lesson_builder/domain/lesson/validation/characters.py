"""Entry points: `parse_character_registry` serves registry loaders; `validate_character_blocks` serves packet and audio validation."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from lesson_builder.domain.lesson.models.blocks import K_READING_VOICE_PROFILES
from lesson_builder.domain.lesson.models.characters import CharacterDefinition
from lesson_builder.domain.lesson.models.characters import CharacterRegistry

K_CHARACTER_SCHEMA_VERSION = 1


def parse_character_registry(raw: Mapping[str, Any]) -> CharacterRegistry:
    """Validate a YAML mapping and return its typed character registry."""
    version = raw.get("schema_version", K_CHARACTER_SCHEMA_VERSION)
    if version != K_CHARACTER_SCHEMA_VERSION:
        raise ValueError(f"characters.yaml schema_version must be {K_CHARACTER_SCHEMA_VERSION!r}")
    characters_raw = raw.get("characters", {})
    if not isinstance(characters_raw, Mapping):
        raise TypeError("characters.yaml characters must be a mapping")
    characters: dict[str, CharacterDefinition] = {}
    character_ids_by_name: dict[str, str] = {}
    for raw_id, raw_definition in characters_raw.items():
        character = _parse_character_definition(raw_id, raw_definition)
        character_id = character.character_id
        if character_id in characters:
            raise ValueError(f"characters.yaml repeats character_id {character_id!r}")
        normalized_name = _normalized_character_name(character.name)
        previous_character_id = character_ids_by_name.setdefault(normalized_name, character_id)
        if previous_character_id != character_id:
            raise ValueError(
                f"characters.yaml assigns speaker name {character.name!r} to both "
                f"{previous_character_id!r} and {character_id!r}"
            )
        characters[character_id] = character
    return CharacterRegistry(characters=characters)


def validate_character_blocks(registry: CharacterRegistry, blocks: Iterable[Mapping[str, Any]]) -> None:
    """Validate explicit character declarations in compiled reading blocks."""
    seen_names: dict[str, str] = {}
    for block in blocks:
        character_id = block.get("character_id")
        if character_id is None:
            continue
        if not isinstance(character_id, str) or not character_id.strip():
            raise ValueError("reading character_id must be a non-empty string")
        character_id = character_id.strip()
        CharacterDefinition.validate_id(character_id)
        definition = registry.characters.get(character_id)
        if definition is None:
            raise ValueError(f"unknown recurring character_id {character_id!r}")
        speaker_name = block.get("speaker_name")
        if not isinstance(speaker_name, str) or not speaker_name.strip():
            raise ValueError(f"character {character_id!r} requires a non-empty speaker_name")
        normalized_name = speaker_name.strip()
        if normalized_name != definition.name:
            raise ValueError(
                f"character {character_id!r} uses speaker_name {normalized_name!r}; "
                f"registry name is {definition.name!r}"
            )
        previous_name = seen_names.setdefault(character_id, normalized_name)
        if previous_name != normalized_name:
            raise ValueError(f"character {character_id!r} has conflicting speaker names")


def _parse_character_definition(raw_id: object, raw_definition: object) -> CharacterDefinition:
    """Validate one raw character entry and return its typed definition."""
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("characters.yaml character IDs must be non-empty strings")
    character_id = raw_id.strip()
    CharacterDefinition.validate_id(character_id)
    if not isinstance(raw_definition, Mapping):
        raise TypeError(f"character {character_id!r} must be a mapping")
    name = raw_definition.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"character {character_id!r} requires a non-empty name")
    voice_profile = raw_definition.get("voice_profile")
    if voice_profile is not None:
        if not isinstance(voice_profile, str) or not voice_profile.strip():
            raise ValueError(f"character {character_id!r} voice_profile must be a non-empty string")
        voice_profile = voice_profile.strip()
        if voice_profile not in K_READING_VOICE_PROFILES:
            raise ValueError(
                f"character {character_id!r} voice_profile must be one of {sorted(K_READING_VOICE_PROFILES)!r}"
            )
    return CharacterDefinition(
        character_id=character_id,
        name=name.strip(),
        voice_profile=voice_profile,
    )


def _normalized_character_name(name: str) -> str:
    """Normalize one registry name using the public speaker-identity rules."""
    normalized = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(normalized.split())


__all__ = ["parse_character_registry", "validate_character_blocks"]
