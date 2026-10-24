"""Behavior tests for the workbench author preview."""

from __future__ import annotations

from pathlib import Path

from lesson_builder.application.operations.author_preview import build_package_preview
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_build_package_preview_given_plan_kind_expect_packet_with_kind(tmp_path: Path):
    package = _copy_fixture(tmp_path)

    preview = build_package_preview(package, source_label="fixture")

    assert preview.errors == ()
    assert preview.packet is not None
    assert preview.packet["kind"] == "grammar"


def test_build_package_preview_given_missing_plan_kind_expect_export_error_and_no_packet(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    plan_path = package / "plan.md"
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("kind: grammar\n", ""),
        encoding="utf-8",
    )

    preview = build_package_preview(package, source_label="fixture")

    assert preview.packet is None
    assert any("must contain a non-empty kind" in error for error in preview.errors)


def _copy_fixture(tmp_path: Path) -> Path:
    package = tmp_path / "question_word_order"
    package.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        (package / name).write_text(
            (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return package
