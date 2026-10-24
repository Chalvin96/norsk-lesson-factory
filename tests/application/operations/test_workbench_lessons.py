"""Behavior tests for workbench lesson listing and contained package resolution."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import list_workbench_lessons
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from tests.domain.curriculum.factories import CurriculumPlanFactory
from tests.domain.curriculum.factories import CurriculumPlanSlotFactory
from tests.workspace.builders import write_curriculum_plan

K_REAL_LESSON_ID = "greet_and_introduce_yourself"
K_REAL_LESSON_PACKAGE = Path(__file__).parents[3] / "content" / "lessons" / K_REAL_LESSON_ID


def test_list_workbench_lessons_given_unordered_slots_and_one_draft_expect_sequence_order_with_markers(
    tmp_path: Path,
) -> None:
    write_curriculum_plan(
        tmp_path,
        CurriculumPlanFactory(
            slots=[
                CurriculumPlanSlotFactory(
                    sequence_index=1, catalog_id="second_lesson", title="Second", cefr_level="A2"
                ),
                CurriculumPlanSlotFactory(sequence_index=0, catalog_id="first_lesson", title="First", cefr_level="A1"),
            ]
        ),
    )
    (tmp_path / "drafts" / "second_lesson").mkdir(parents=True)

    lessons = list_workbench_lessons(tmp_path)

    assert [(item.lesson_id, item.title, item.cefr_level, item.sequence_index, item.source) for item in lessons] == [
        ("first_lesson", "First", "A1", 0, "missing"),
        ("second_lesson", "Second", "A2", 1, "draft"),
    ]


def test_list_workbench_lessons_given_slot_with_unsafe_catalog_id_expect_value_error(tmp_path: Path) -> None:
    write_curriculum_plan(tmp_path, CurriculumPlanFactory(slots=[CurriculumPlanSlotFactory(catalog_id="../escape")]))

    with pytest.raises(ValueError, match="invalid lesson slug"):
        list_workbench_lessons(tmp_path)


def test_resolve_lesson_package_given_draft_and_canonical_expect_draft_wins(tmp_path: Path) -> None:
    _copy_real_lesson(tmp_path / "content" / "lessons" / K_REAL_LESSON_ID)
    _copy_real_lesson(tmp_path / "drafts" / K_REAL_LESSON_ID)

    package = resolve_lesson_package(tmp_path, K_REAL_LESSON_ID)

    assert package.source == "draft"
    assert package.source_label == f"drafts/{K_REAL_LESSON_ID}"
    assert package.path == (tmp_path / "drafts" / K_REAL_LESSON_ID).resolve()


def test_resolve_lesson_package_given_draft_shadow_and_canonical_only_expect_canonical_source(tmp_path: Path) -> None:
    _copy_real_lesson(tmp_path / "content" / "lessons" / K_REAL_LESSON_ID)
    _copy_real_lesson(tmp_path / "drafts" / K_REAL_LESSON_ID)

    package = resolve_lesson_package(tmp_path, K_REAL_LESSON_ID, canonical_only=True)

    assert package.source == "canonical"
    assert package.source_label == f"content/lessons/{K_REAL_LESSON_ID}"
    assert package.path == (tmp_path / "content" / "lessons" / K_REAL_LESSON_ID).resolve()


def test_resolve_lesson_package_given_canonical_source_without_draft_expect_canonical_without_seeding_draft(
    tmp_path: Path,
) -> None:
    _copy_real_lesson(tmp_path / "content" / "lessons" / K_REAL_LESSON_ID)

    package = resolve_lesson_package(tmp_path, K_REAL_LESSON_ID)

    assert package.source == "canonical"
    assert package.source_label == f"content/lessons/{K_REAL_LESSON_ID}"
    assert package.path == (tmp_path / "content" / "lessons" / K_REAL_LESSON_ID).resolve()
    assert not (tmp_path / "drafts").exists()


@pytest.mark.parametrize("lesson_id", ["../outside", "..", "/etc", "Bad-Slug", "a/b", ""])
def test_resolve_lesson_package_given_unsafe_or_invalid_id_expect_value_error(tmp_path: Path, lesson_id: str) -> None:
    (tmp_path / "outside").mkdir()

    with pytest.raises(ValueError, match="invalid lesson slug"):
        resolve_lesson_package(tmp_path, lesson_id)


def test_resolve_lesson_package_given_unknown_id_expect_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        resolve_lesson_package(tmp_path, "missing_lesson")


def test_resolve_lesson_package_given_draft_file_symlink_outside_drafts_root_expect_value_error(
    tmp_path: Path,
) -> None:
    draft = tmp_path / "drafts" / "demo"
    _copy_real_lesson(draft)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (draft / "lesson.md").unlink()
    (draft / "lesson.md").symlink_to(outside)

    with pytest.raises(ValueError, match="missing lesson.md"):
        resolve_lesson_package(tmp_path, "demo")


def test_resolve_lesson_package_given_draft_dir_symlink_outside_drafts_root_expect_value_error(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside_package"
    _copy_real_lesson(outside)
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "demo").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes its draft root"):
        resolve_lesson_package(tmp_path, "demo")


def test_build_workbench_preview_given_draft_package_expect_draft_label_and_packet(tmp_path: Path) -> None:
    _copy_real_lesson(tmp_path / "drafts" / K_REAL_LESSON_ID)
    package = resolve_lesson_package(tmp_path, K_REAL_LESSON_ID)

    preview = build_workbench_preview(package)

    assert preview.source_label == f"drafts/{K_REAL_LESSON_ID}"
    assert preview.packet is not None
    assert preview.errors == ()


def test_build_workbench_preview_given_source_change_expect_fresh_preview(tmp_path: Path) -> None:
    _copy_real_lesson(tmp_path / "content" / "lessons" / K_REAL_LESSON_ID)
    package = resolve_lesson_package(tmp_path, K_REAL_LESSON_ID)
    original = build_workbench_preview(package)
    changed_source = original.lesson_source.replace("A first meeting", "A friendly first meeting", 1)
    (package.path / "lesson.md").write_text(changed_source, encoding="utf-8")

    changed = build_workbench_preview(package)

    assert "A friendly first meeting" in changed.lesson_source
    assert changed.lesson_source != original.lesson_source


def _copy_real_lesson(destination: Path) -> None:
    shutil.copytree(K_REAL_LESSON_PACKAGE, destination)
