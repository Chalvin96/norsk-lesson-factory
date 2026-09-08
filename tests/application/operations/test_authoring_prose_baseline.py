"""Entry point: `require_authoring_prose_baseline` behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.application.operations.authoring_prose_baseline import collect_authoring_prose_fingerprints
from lesson_builder.application.operations.authoring_prose_baseline import require_authoring_prose_baseline
from lesson_builder.application.operations.authoring_prose_baseline import require_authoring_prose_package


def test_authoring_prose_baseline_given_legacy_match_then_changed_or_added_match_expect_rejects(tmp_path: Path) -> None:
    lesson_file = tmp_path / "content" / "lessons" / "sample" / "lesson.md"
    lesson_file.parent.mkdir(parents=True)
    lesson_file.write_text("**Activity request: old**\n", encoding="utf-8")
    baseline = tmp_path / "scripts" / "authoring_prose_baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps({"version": 1, "lessons": collect_authoring_prose_fingerprints(tmp_path)}), encoding="utf-8"
    )

    assert "sample" in require_authoring_prose_baseline(tmp_path)
    require_authoring_prose_package(tmp_path, "sample", "**Activity request: old**\n")
    with pytest.raises(ValueError, match="sample/line 1 internal-request-anchor"):
        require_authoring_prose_package(tmp_path, "sample", "**Activity request: changed**\n")
    lesson_file.write_text("**Activity request: changed**\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sample/line 1 internal-request-anchor"):
        require_authoring_prose_baseline(tmp_path)
    lesson_file.write_text("**Activity request: old**\n**Activity request: old**\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sample/line 2 internal-request-anchor"):
        require_authoring_prose_baseline(tmp_path)


def test_authoring_prose_baseline_given_legacy_match_removed_expect_allows_cleanup(tmp_path: Path) -> None:
    lesson_file = tmp_path / "content" / "lessons" / "sample" / "lesson.md"
    lesson_file.parent.mkdir(parents=True)
    lesson_file.write_text("**Activity request: old**\n", encoding="utf-8")
    baseline = tmp_path / "scripts" / "authoring_prose_baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps({"version": 1, "lessons": collect_authoring_prose_fingerprints(tmp_path)}), encoding="utf-8"
    )

    lesson_file.write_text("Read the dialogue and choose a reply.\n", encoding="utf-8")

    assert require_authoring_prose_baseline(tmp_path) == {}
