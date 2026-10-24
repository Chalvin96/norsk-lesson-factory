"""Entry points: `parse_workbench_settings` and `render_workbench_settings` handle settings YAML."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from typing import cast

import yaml

from lesson_builder.formats.yaml import load_unique_yaml


def parse_workbench_settings(source: str) -> dict[str, Any]:
    """Load unique YAML mapping content for application-level schema validation."""
    value = load_unique_yaml(source)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("workbench settings must be a YAML mapping")
    return value


def render_workbench_settings(settings: Mapping[str, object]) -> str:
    """Serialize validated settings as stable, human-readable YAML."""
    return cast(str, yaml.safe_dump(dict(settings), allow_unicode=True, sort_keys=False))


__all__ = ["parse_workbench_settings", "render_workbench_settings"]
