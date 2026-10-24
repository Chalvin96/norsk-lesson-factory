"""Entry points: workspace assembly, snapshots and workbench curriculum builders for behavior tests.

The builder materializes one complete committed workspace (approved catalog,
family registry, character and terminology registries, lesson package,
committed curriculum plan, committed distribution) under a temporary root and
snapshots the tree so tests can prove the check stays read-only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml

from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.materialize_curriculum import materialize_curriculum_plan
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.services.plan_policy import hash_catalog_content
from lesson_builder.domain.curriculum.services.plan_policy import hash_sequence_content
from lesson_builder.workspace.paths import WorkspacePaths
from tests.domain.curriculum.factories import CurriculumPlanFactory
from tests.domain.curriculum.factories import CurriculumPlanSlotFactory
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT

K_LESSON_FILES = ("plan.md", "lesson.md", "exercises.yaml")
K_LESSON_ID = "alpha_dialogue"
K_PREREQUISITE_LESSON_ID = "beta_prerequisite_lesson"


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


def assemble_prerequisite_order_workspace(tmp_path: Path, *, prerequisite_first: bool) -> None:
    """Plan a second lesson the main lesson requires, ordered before or after it."""
    assemble_valid_workspace(tmp_path)
    prerequisite_package = tmp_path / "content" / "lessons" / K_PREREQUISITE_LESSON_ID
    shutil.copytree(tmp_path / "content" / "lessons" / K_LESSON_ID, prerequisite_package)
    for name in K_LESSON_FILES:
        path = prerequisite_package / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(K_LESSON_ID, K_PREREQUISITE_LESSON_ID), encoding="utf-8"
        )
    plan = read_curriculum(tmp_path)
    dependent = plan.slots[0].model_copy(update={"prerequisites": [K_PREREQUISITE_LESSON_ID]})
    prerequisite = plan.slots[0].model_copy(update={"catalog_id": K_PREREQUISITE_LESSON_ID})
    ordered = [prerequisite, dependent] if prerequisite_first else [dependent, prerequisite]
    reindexed = [slot.model_copy(update={"sequence_index": index}) for index, slot in enumerate(ordered)]
    write_curriculum_plan(tmp_path, plan.model_copy(update={"slots": reindexed}))
    export_distribution(tmp_path)


def workspace_snapshot(workspace: Path) -> dict[str, bytes]:
    """Capture every file's relative path and bytes for read-only proofs."""
    return {
        str(path.relative_to(workspace)): path.read_bytes() for path in sorted(workspace.rglob("*")) if path.is_file()
    }


def write_curriculum_plan(workspace: Path, plan: CurriculumPlan) -> None:
    """Write ``plan`` as the committed curriculum plan without catalog materialization."""
    path = WorkspacePaths(workspace).curriculum_plan
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False), encoding="utf-8")


def build_workbench_curriculum_repository(tmp_path: Path) -> Path:
    """Build an isolated committed curriculum and one complete format exemplar."""
    root = Path(__file__).parents[2]
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(root / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    catalog = {"schema_version": 3, "entries": [{"id": lesson_id}, {"id": "second"}], "summary": {}}
    catalog_path = tmp_path / "content" / "catalog" / "approved" / "catalog.yaml"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(yaml.safe_dump(catalog))
    write_curriculum_plan(
        tmp_path,
        CurriculumPlanFactory(
            source_catalog="content/catalog/approved/catalog.yaml",
            source_catalog_hash=hash_catalog_content(catalog),
            source_sequence="",
            source_sequence_hash=hash_sequence_content(None),
            slots=[
                CurriculumPlanSlotFactory(
                    catalog_id=lesson_id, catalog_kind="communicative", sequence_index=0, family_id="social_interaction"
                ),
                CurriculumPlanSlotFactory(catalog_id="second", sequence_index=1, prerequisites=[lesson_id]),
            ],
        ),
    )
    plan = tmp_path / "content" / "curriculum" / "plan.yaml"
    plan.write_text("# keep this comment\n" + plan.read_text().replace("run_id: test-run", "run_id: 'test-run'"))
    for arguments in (
        ("init", "-b", "authoring"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
        ("add", "."),
        ("commit", "--no-verify", "-m", "initial"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    return tmp_path


def build_workbench_author_response(root: Path) -> str:
    """Return a complete exemplar adapted to the new slot used by creation tests."""
    package = root / "content" / "lessons" / "greet_and_introduce_yourself"
    texts = {
        name: (package / name).read_text().replace("greet_and_introduce_yourself", "new_lesson")
        for name in ("plan.md", "lesson.md", "exercises.yaml")
    }
    plan = texts["plan.md"].split("---", 2)
    metadata = yaml.safe_load(plan[1])
    metadata.update(
        title="New title",
        learner_outcome="New outcome.",
        family_id="",
        teaching_points=[
            {
                "id": "new_lesson:greeting",
                "source_owner": "new_lesson",
                "statement": "Choose a greeting for the setting.",
            },
            {"id": "new_lesson:name", "source_owner": "new_lesson", "statement": "Introduce yourself using Jeg heter."},
        ],
    )
    texts["plan.md"] = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---" + plan[2]
    lesson = texts["lesson.md"].split("---", 2)
    header = yaml.safe_load(lesson[1])
    header.update(title="New title", goal="New outcome.")
    texts["lesson.md"] = "---\n" + yaml.safe_dump(header, allow_unicode=True, sort_keys=False) + "---" + lesson[2]
    return json.dumps(
        {
            "plan": metadata,
            "plan_body": plan[2].strip(),
            "lesson_md": texts["lesson.md"],
            "exercises": yaml.safe_load(texts["exercises.yaml"]),
        }
    )


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
