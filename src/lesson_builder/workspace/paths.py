"""Entry point: `get_workspace_root` resolves the installed workspace root.

The paths object centralizes workspace-owned authoring and distribution paths so
commands do not encode source locations independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lesson_builder.workspace.settings import K_WORKSPACE_AUTHORING_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_CATALOG_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_CONTENT_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_CURRICULUM_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_DIST_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_LESSONS_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_ROOT
from lesson_builder.workspace.settings import K_WORKSPACE_TERMINOLOGY_DIR
from lesson_builder.workspace.settings import K_WORKSPACE_TERMINOLOGY_FILE


def get_workspace_root() -> Path:
    """Return the workspace root for the installed source tree."""
    return K_WORKSPACE_ROOT


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolve tracked authoring paths from one workspace root."""

    root: Path

    @property
    def content_root(self) -> Path:
        return self.root / K_WORKSPACE_CONTENT_DIR

    @property
    def authoring_root(self) -> Path:
        return self.content_root / K_WORKSPACE_AUTHORING_DIR

    @property
    def character_registry(self) -> Path:
        return self.authoring_root / "characters.yaml"

    @property
    def terminology_registry(self) -> Path:
        return self.content_root / K_WORKSPACE_TERMINOLOGY_DIR / K_WORKSPACE_TERMINOLOGY_FILE

    @property
    def catalog_root(self) -> Path:
        return self.content_root / K_WORKSPACE_CATALOG_DIR

    @property
    def catalog_file(self) -> Path:
        return self.catalog_root / "approved" / "catalog.yaml"

    @property
    def catalog_registry(self) -> Path:
        return self.catalog_root / "registry" / "catalog_families.yaml"

    @property
    def curriculum_root(self) -> Path:
        return self.content_root / K_WORKSPACE_CURRICULUM_DIR

    @property
    def curriculum_plan(self) -> Path:
        return self.curriculum_root / "plan.yaml"

    @property
    def curriculum_sequence(self) -> Path:
        return self.curriculum_root / "sequence.yaml"

    @property
    def lessons_root(self) -> Path:
        return self.content_root / K_WORKSPACE_LESSONS_DIR

    @property
    def lesson_approvals_root(self) -> Path:
        return self.content_root / "approvals" / K_WORKSPACE_LESSONS_DIR

    @property
    def dependency_review_scratch_root(self) -> Path:
        return self.root / "store" / "scratch" / "curriculum-dependencies"

    @property
    def curriculum_scratch_root(self) -> Path:
        return self.root / "store" / "scratch" / "curriculum"

    @property
    def dist_root(self) -> Path:
        return self.root / K_WORKSPACE_DIST_DIR


__all__ = ["K_WORKSPACE_ROOT", "WorkspacePaths", "get_workspace_root"]
