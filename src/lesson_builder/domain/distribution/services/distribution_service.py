"""Entry point: `DistributionService` creates learner-facing distribution data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lesson_builder.domain.lesson.models import Lesson
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.validation.characters import validate_character_blocks
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict


class DistributionService:
    """Expose pure projection operations for learner-facing distribution data."""

    def create_lesson_packet(
        self,
        lesson: Lesson,
        *,
        kind: str | None = None,
        language: str = "nb-NO",
        audio: list[dict[str, Any]] | None = None,
        audio_bindings: Mapping[str, str] | None = None,
        character_registry: CharacterRegistry | None = None,
        allow_unregistered_characters: bool = False,
    ) -> dict[str, Any]:
        """Create one validated public lesson packet from an internal lesson."""
        reading_blocks = _reading_blocks(lesson)
        if (
            character_registry is None
            and not allow_unregistered_characters
            and any(block.get("character_id") is not None for block in reading_blocks)
        ):
            raise ValueError("character registry is required when a lesson uses character_id")
        if character_registry is not None:
            validate_character_blocks(character_registry, reading_blocks)
        return to_export_dict(
            lesson,
            kind=kind,
            language=language,
            audio=audio,
            audio_bindings=audio_bindings,
            character_voice_profiles=(
                None
                if character_registry is None
                else {
                    character_id: definition.voice_profile
                    for character_id, definition in character_registry.characters.items()
                }
            ),
        )

    def create_distribution_catalog(
        self,
        packets: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> dict[str, Any]:
        """Create the ordered catalog that indexes the distribution packets."""
        lessons: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for position, (packet, metadata) in enumerate(packets):
            lesson_id = str(packet["id"])
            if lesson_id in seen_ids:
                raise ValueError(f"distribution catalog repeats lesson id {lesson_id!r}")
            seen_ids.add(lesson_id)
            entry: dict[str, Any] = {"lesson_id": lesson_id, "position": position}
            family_id = metadata.get("family_id")
            if isinstance(family_id, str) and family_id:
                entry["family_id"] = family_id
            lessons.append(entry)
        return {"lessons": lessons}


def _reading_blocks(lesson: Lesson) -> list[Mapping[str, Any]]:
    """Return every internal reading block, including nested callouts."""
    raw = lesson.model_dump(mode="json")
    readings: list[Mapping[str, Any]] = []
    for element in raw["elements"]:
        if element.get("element_kind") == "section":
            readings.extend(_nested_reading_blocks(element.get("blocks", [])))
    return readings


def _nested_reading_blocks(blocks: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
    """Collect reading blocks recursively from one internal block list."""
    readings: list[Mapping[str, Any]] = []
    for block in blocks:
        if block.get("kind") == "reading":
            readings.append(block)
        elif block.get("kind") == "callout":
            readings.extend(_nested_reading_blocks(block.get("blocks", [])))
    return readings


__all__ = ["DistributionService"]
