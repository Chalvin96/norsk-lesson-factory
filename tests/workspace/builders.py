"""Shared canonical-workspace assembly for workspace and CLI behavior tests.

The builder materializes one complete committed workspace (approved catalog,
family registry, character and terminology registries, lesson package,
committed curriculum plan, committed distribution) under a temporary root and
snapshots the tree so tests can prove the check stays read-only.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.materialize_curriculum import materialize_curriculum_plan
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT

K_LESSON_FILES = ("plan.md", "lesson.md", "exercises.yaml")
K_LESSON_ID = "alpha_dialogue"


def assemble_valid_workspace(workspace: Path) -> None:
    """Materialize one complete canonical workspace with a committed distribution."""
    _write_approved_catalog(workspace)
    _write_catalog_family_registry(workspace)
    _write_character_registry(workspace)
    _write_terminology_registry(workspace)
    _copy_lesson_package(workspace)
    materialize_curriculum_plan(
        repo_root=workspace,
        run_id="workspace-check",
        auto_approve=True,
        commit=True,
    )
    export_distribution(workspace)


def workspace_snapshot(workspace: Path) -> dict[str, bytes]:
    """Capture every file's relative path and bytes for read-only proofs."""
    return {
        str(path.relative_to(workspace)): path.read_bytes() for path in sorted(workspace.rglob("*")) if path.is_file()
    }


def _write_approved_catalog(workspace: Path) -> None:
    catalog_path = workspace / "content" / "catalog" / "approved" / "catalog.yaml"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 3,
                "catalog_status": "complete",
                "snapshot": "test",
                "dependency_graph": {"status": "complete"},
                "entries": [
                    {
                        "id": K_LESSON_ID,
                        "catalog_kind": "grammar",
                        "title": "Alpha dialogue",
                        "cefr_tags": ["A1"],
                        "prerequisites": [],
                        "helpful_prerequisites": [],
                        "owner_scope": {
                            "status": "atomic",
                            "learner_decision": "Use the target decision in context.",
                            "assessment_operation": "Choose the form that matches the context.",
                            "review_status": "approved",
                            "reviewed_by": "test-human",
                        },
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_catalog_family_registry(workspace: Path) -> None:
    registry_path = workspace / "content" / "catalog" / "registry" / "catalog_families.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("families: {}\n", encoding="utf-8")


def _write_character_registry(workspace: Path) -> None:
    registry_path = workspace / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  anna:\n    name: Anna\n    voice_profile: feminine\n",
        encoding="utf-8",
    )


def _write_terminology_registry(workspace: Path) -> None:
    registry_path = workspace / "content" / "terminology" / "glossary.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        "schema_version: '1'\nconcepts:\n  - id: finite-verb\n    preferred_label: finite verb\n    scope: active\n",
        encoding="utf-8",
    )


def _copy_lesson_package(workspace: Path) -> None:
    package = workspace / "content" / "lessons" / K_LESSON_ID
    package.mkdir(parents=True)
    for name in K_LESSON_FILES:
        source = (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        (package / name).write_text(source.replace("question_word_order", K_LESSON_ID), encoding="utf-8")
