"""Not a check itself — workspace-owned path constants.

These constants describe where the committed authoring workspace keeps its
canonical inputs and derived distribution, plus the legacy root names that must not
reappear outside ``content/``.
"""

from __future__ import annotations

from pathlib import Path

K_WORKSPACE_ROOT_MARKER = "pyproject.toml"
K_WORKSPACE_CONTENT_DIR = "content"
K_WORKSPACE_AUTHORING_DIR = "authoring"
K_WORKSPACE_CATALOG_DIR = "catalog"
K_WORKSPACE_CURRICULUM_DIR = "curriculum"
K_WORKSPACE_LESSONS_DIR = "lessons"
K_WORKSPACE_TERMINOLOGY_DIR = "terminology"
K_WORKSPACE_DIST_DIR = "dist"
K_WORKSPACE_TERMINOLOGY_FILE = "glossary.yaml"
K_WORKSPACE_LEGACY_ROOT_DIRS = ("authoring", "catalog", "curriculum", "lessons")


def _find_workspace_root() -> Path:
    """Find the source workspace by its project marker, not package depth."""
    for parent in Path(__file__).resolve().parents:
        if (parent / K_WORKSPACE_ROOT_MARKER).is_file():
            return parent
    return Path(__file__).resolve().parent


K_WORKSPACE_ROOT = _find_workspace_root()

__all__ = [
    "K_WORKSPACE_ROOT",
    "K_WORKSPACE_ROOT_MARKER",
    "K_WORKSPACE_CONTENT_DIR",
    "K_WORKSPACE_AUTHORING_DIR",
    "K_WORKSPACE_CATALOG_DIR",
    "K_WORKSPACE_CURRICULUM_DIR",
    "K_WORKSPACE_LESSONS_DIR",
    "K_WORKSPACE_TERMINOLOGY_DIR",
    "K_WORKSPACE_DIST_DIR",
    "K_WORKSPACE_TERMINOLOGY_FILE",
    "K_WORKSPACE_LEGACY_ROOT_DIRS",
]
