"""Behavior tests for the current Markdown/YAML lesson-owner snapshot."""

from __future__ import annotations

import shutil
from pathlib import Path

from lesson_builder.workflow.catalog_design.context import load_existing_lessons
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_existing_snapshot_given_source_package_expect_export_objective_context(tmp_path: Path):
    source_dir = tmp_path / "content" / "lessons" / "question_word_order"
    source_dir.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source_dir / name)

    snapshot = load_existing_lessons(tmp_path)

    assert snapshot[0].title == "Norwegian main-clause word order: questions and fronting"
    assert snapshot[0].teaching_point_ids == ["existing:question_word_order:obj-question-order"]
    assert snapshot[0].objective_summary.startswith("Form and recognise Norwegian")


def test_existing_snapshot_given_invalid_readable_lesson_expect_identity_retained(tmp_path: Path):
    source_dir = tmp_path / "content" / "lessons" / "question_word_order"
    source_dir.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source_dir / name)
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace("Jeg jobber hjemme i dag.", "Jeg jobber hjemme but i dag."),
        encoding="utf-8",
    )

    snapshot = load_existing_lessons(tmp_path)

    assert [lesson.slug for lesson in snapshot] == ["question_word_order"]
