"""Entry points: ``load_character_registry`` serves repository checks; ``load_optional_character_registry`` serves export, audio, and authoring workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.validation.characters import parse_character_registry
from lesson_builder.workspace.paths import WorkspacePaths


def load_character_registry(repo_root: str | Path) -> CharacterRegistry:
    """Load the tracked provider-neutral recurring-character registry."""
    path = WorkspacePaths(Path(repo_root)).character_registry
    if not path.is_file():
        raise FileNotFoundError(f"character registry not found at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("characters.yaml must be a YAML mapping")
    return parse_character_registry(raw)


def load_optional_character_registry(
    source_root: str | Path, *, search_parents: bool = True
) -> CharacterRegistry | None:
    """Load an optional character registry, optionally walking parent roots."""
    source_path = Path(source_root)
    roots: tuple[Path, ...]
    if not search_parents:
        roots = (source_path,)
    elif source_path.is_dir():
        roots = (source_path, *source_path.parents)
    else:
        roots = tuple(source_path.parents)
    for root in roots:
        if WorkspacePaths(root).character_registry.is_file():
            return load_character_registry(root)
    return None


__all__ = ["load_character_registry", "load_optional_character_registry"]
