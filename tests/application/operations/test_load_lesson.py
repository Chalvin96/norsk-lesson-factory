"""Entry point: tests for the application lesson source loader."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.load_lesson import load_lesson
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_load_lesson_given_authored_files_expect_lesson_without_sidecar(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source / name)

    lesson = load_lesson(source)

    assert lesson.key == "question_word_order"
    assert not (source / ".meta.json").exists()


def test_load_lesson_given_material_audit_finding_expect_blocked(tmp_path: Path):
    source = tmp_path / "source"
    shutil.copytree(K_CATALOG_PACKAGE_FIXTURE_ROOT, source)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("text: hjemme.", "text: hjemme", 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)
    (source / "lesson.md").unlink()

    with pytest.raises(ValueError, match="mechanical exercise preflight"):
        load_lesson(source, audit=audit)
