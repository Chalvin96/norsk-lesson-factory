"""Behavior tests for application-owned recurring-character file loading."""

from __future__ import annotations

from pathlib import Path

from lesson_builder.application.operations.load_characters import load_character_registry
from lesson_builder.application.operations.load_characters import load_optional_character_registry


def test_load_character_registry_given_repo_root_expect_empty_registry(tmp_path: Path):
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("schema_version: 1\ncharacters: {}\n", encoding="utf-8")

    registry = load_character_registry(tmp_path)

    assert registry.characters == {}


def test_load_optional_character_registry_given_nested_source_and_registry_expect_registry(
    tmp_path: Path,
):
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  anna:\n    name: Anna\n",
        encoding="utf-8",
    )
    source_dir = tmp_path / "store" / "scratch" / "run" / "generated"
    source_dir.mkdir(parents=True)

    registry = load_optional_character_registry(source_dir)

    assert registry is not None
    assert registry.characters["anna"].name == "Anna"


def test_load_optional_character_registry_given_source_without_registry_expect_none(
    tmp_path: Path,
):
    source_dir = tmp_path / "store" / "scratch" / "run" / "generated"
    source_dir.mkdir(parents=True)

    assert load_optional_character_registry(source_dir) is None
