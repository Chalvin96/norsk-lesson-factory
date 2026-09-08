"""Entry points: `load_terminology_registry`, `load_terminology_bans`.

`load_terminology_registry` loads and validates the glossary; `load_terminology_bans`
projects its banned phrases for lesson checks.
CLI commands, workspace checks, curriculum materialization, and lesson
generation call these operations.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry
from lesson_builder.domain.lesson.validation.terminology_registry import parse_terminology_registry
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.paths import get_workspace_root


def load_terminology_registry(
    path: str | Path | None = None,
    *,
    use_cache: bool = True,
) -> TerminologyRegistry:
    """Load and validate the terminology registry from ``glossary.yaml``."""
    resolved = Path(path).resolve() if path else WorkspacePaths(get_workspace_root()).terminology_registry
    return _load_registry_cached(resolved) if use_cache else _load_registry(resolved)


def load_terminology_bans(path: str | Path | None = None) -> TerminologyBans:
    """Project the registry's hard bans and prose-tell phrases."""
    registry = load_terminology_registry(path)
    return TerminologyBans(
        banned_phrases=registry.banned_phrases,
        prose_tells=registry.prose_tell_phrases,
    )


@lru_cache(maxsize=8)
def _load_registry_cached(resolved: Path) -> TerminologyRegistry:
    return _load_registry(resolved)


def _load_registry(resolved: Path) -> TerminologyRegistry:
    if not resolved.exists():
        raise FileNotFoundError(f"terminology registry not found: {resolved}")
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"terminology registry must be a YAML mapping: {resolved}")
    return parse_terminology_registry(raw)


__all__ = ["load_terminology_bans", "load_terminology_registry"]
