"""Behavior tests for explicit recurring-character registry validation."""

from __future__ import annotations

import pytest

from lesson_builder.domain.lesson.validation.characters import parse_character_registry
from lesson_builder.domain.lesson.validation.characters import validate_character_blocks


def test_parse_character_registry_given_valid_character_expect_definition():
    registry = parse_character_registry({"schema_version": 1, "characters": {"anna": {"name": "Anna"}}})

    validate_character_blocks(
        registry,
        [
            {
                "character_id": "anna",
                "speaker_id": "anna-local",
                "speaker_name": "Anna",
                "dialogue_id": "d1",
            }
        ],
    )


def test_parse_character_registry_given_voice_profile_expect_provider_neutral_definition():
    registry = parse_character_registry(
        {
            "schema_version": 1,
            "characters": {
                "jonas": {"name": "Jonas", "voice_profile": "masculine"},
                "mina": {"name": "Mina", "voice_profile": "feminine"},
            },
        }
    )

    assert registry.characters["jonas"].voice_profile == "masculine"
    assert registry.characters["mina"].voice_profile == "feminine"


def test_parse_character_registry_given_unknown_voice_profile_expect_value_error():
    with pytest.raises(ValueError, match="voice_profile must be one of"):
        parse_character_registry(
            {
                "schema_version": 1,
                "characters": {"jonas": {"name": "Jonas", "voice_profile": "robotic"}},
            }
        )


def test_parse_character_registry_given_same_normalized_name_expect_value_error():
    with pytest.raises(ValueError, match="assigns speaker name 'mina' to both 'mina-one' and 'mina-two'"):
        parse_character_registry(
            {
                "schema_version": 1,
                "characters": {
                    "mina-one": {"name": "Mina"},
                    "mina-two": {"name": " mina "},
                },
            }
        )


def test_character_registry_given_unknown_character_expect_validation_error():
    registry = parse_character_registry({"schema_version": 1, "characters": {}})

    with pytest.raises(ValueError, match="unknown recurring character_id"):
        validate_character_blocks(
            registry,
            [
                {
                    "character_id": "anna",
                    "speaker_id": "anna-local",
                    "speaker_name": "Anna",
                    "dialogue_id": "d1",
                }
            ],
        )


def test_character_registry_given_conflicting_display_name_expect_validation_error():
    registry = parse_character_registry({"schema_version": 1, "characters": {"anna": {"name": "Anna"}}})

    with pytest.raises(ValueError, match="registry name"):
        validate_character_blocks(
            registry,
            [
                {
                    "character_id": "anna",
                    "speaker_id": "anna-local",
                    "speaker_name": "Anne",
                    "dialogue_id": "d1",
                }
            ],
        )
