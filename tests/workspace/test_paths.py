"""Behavior tests for canonical workspace content paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import lesson_builder.workspace.settings as workspace_settings
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.paths import get_workspace_root


def test_workspace_paths_given_workspace_root_expect_canonical_paths_are_centralized(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path)

    assert paths.content_root == tmp_path / "content"
    assert paths.character_registry == tmp_path / "content" / "authoring" / "characters.yaml"
    assert paths.terminology_registry == tmp_path / "content" / "terminology" / "glossary.yaml"
    assert paths.catalog_file == tmp_path / "content" / "catalog" / "approved" / "catalog.yaml"
    assert paths.catalog_registry == tmp_path / "content" / "catalog" / "registry" / "catalog_families.yaml"
    assert paths.curriculum_plan == tmp_path / "content" / "curriculum" / "plan.yaml"
    assert paths.lessons_root == tmp_path / "content" / "lessons"
    assert paths.lesson_approvals_root == tmp_path / "content" / "approvals" / "lessons"
    assert paths.terminology_registry == tmp_path / "content" / "terminology" / "glossary.yaml"
    assert paths.dependency_review_scratch_root == tmp_path / "store" / "scratch" / "curriculum-dependencies"
    assert paths.curriculum_scratch_root == tmp_path / "store" / "scratch" / "curriculum"
    assert paths.dist_root == tmp_path / "dist"


def test_get_workspace_root_given_installed_source_tree_expect_workspace_root() -> None:
    root = get_workspace_root()

    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "lesson_builder" / "workspace" / "paths.py").is_file()


def test_workspace_settings_given_installed_module_without_project_marker_expect_import_succeeds(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "site-packages" / "lesson_builder" / "workspace"
    package_dir.mkdir(parents=True)
    installed_settings = package_dir / "settings.py"
    installed_settings.write_text(Path(workspace_settings.__file__).read_text(encoding="utf-8"), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("installed_workspace_settings", installed_settings)
    assert spec is not None and spec.loader is not None
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)

    assert package_dir == installed.K_WORKSPACE_ROOT
