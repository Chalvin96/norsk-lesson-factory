"""Entry point: ``import_lesson`` and ``regenerate_dist``.

``import_lesson`` registers an externally-authored internal lesson under
``data/lessons/<slug>.json`` plus a ledger entry. ``regenerate_dist`` derives
``dist/lessons/<slug>.json`` from the data lesson projection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.pipeline.lesson_import import (
    K_IMPORT_DEFAULT_STATUS,
    LessonImportError,
    import_lesson,
    regenerate_dist,
)
from lesson_builder.schema import Lesson

ROOT = Path(__file__).resolve().parents[2]


def _real_internal_lesson(slug: str = "ordinal_numbers") -> dict:
    return json.loads((ROOT / "data" / "lessons" / f"{slug}.json").read_text(encoding="utf-8"))


def test_import_lesson_given_internal_lesson_file_expect_registers_with_default_status(
    tmp_path: Path,
):
    src = tmp_path / "ordinal_numbers.json"
    src.write_text(json.dumps(_real_internal_lesson(), ensure_ascii=False), encoding="utf-8")
    repo = tmp_path / "repo"

    result = import_lesson(src, repo_root=repo, output_root=repo)

    assert result["slug"] == "ordinal_numbers"
    assert result["status"] == K_IMPORT_DEFAULT_STATUS
    assert result["created"] == 1
    assert (repo / "data" / "lessons" / "ordinal_numbers.json").exists()
    assert not (repo / "dist" / "lessons" / "ordinal_numbers.json").exists()
    assert result["acceptance_log_path"] == str(repo / "data" / "lesson_acceptance_log.jsonl")
    entry = json.loads(
        Path(result["acceptance_log_path"]).read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert entry["status"] == "imported_unverified"
    assert entry["source_kind"] == "dist_import"
    assert entry["export_path"] == "dist/lessons/ordinal_numbers.json"


def test_import_lesson_given_invalid_file_expect_raises_and_nothing_written(tmp_path: Path):
    src = tmp_path / "bad.json"
    src.write_text('{"not": "a lesson"}', encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(LessonImportError):
        import_lesson(src, repo_root=repo, output_root=repo)

    assert not (repo / "data" / "lessons" / "bad.json").exists()
    assert not (repo / "dist" / "lessons" / "bad.json").exists()


def test_import_lesson_given_slug_override_expect_uses_override(tmp_path: Path):
    src = tmp_path / "some_file.json"
    src.write_text(json.dumps(_real_internal_lesson(), ensure_ascii=False), encoding="utf-8")
    repo = tmp_path / "repo"

    result = import_lesson(src, slug="custom_slug", repo_root=repo, output_root=repo)

    assert result["slug"] == "custom_slug"
    assert (repo / "data" / "lessons" / "custom_slug.json").exists()


def test_import_lesson_given_explicit_accepted_status_expect_reflects_in_ledger(tmp_path: Path):
    src = tmp_path / "ordinal_numbers.json"
    src.write_text(json.dumps(_real_internal_lesson(), ensure_ascii=False), encoding="utf-8")
    repo = tmp_path / "repo"

    result = import_lesson(src, status="accepted", repo_root=repo, output_root=repo)

    assert result["status"] == "accepted"
    entry = json.loads(
        Path(result["acceptance_log_path"]).read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert entry["status"] == "accepted"


def test_import_lesson_given_reimport_same_content_expect_idempotent(tmp_path: Path):
    src = tmp_path / "ordinal_numbers.json"
    src.write_text(json.dumps(_real_internal_lesson(), ensure_ascii=False), encoding="utf-8")
    repo = tmp_path / "repo"

    first = import_lesson(src, repo_root=repo, output_root=repo)
    second = import_lesson(src, repo_root=repo, output_root=repo)

    assert first["created"] == 1
    assert second["unchanged"] == 1
    assert second["created"] == 0


def test_regenerate_dist_given_data_lessons_expect_idempotent_projection(tmp_path: Path):
    repo = tmp_path / "repo"
    lessons_dir = repo / "data" / "lessons"
    lessons_dir.mkdir(parents=True)
    lesson_path = lessons_dir / "ordinal_numbers.json"
    lesson_path.write_text(
        json.dumps(_real_internal_lesson(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    first = regenerate_dist(repo)
    second = regenerate_dist(repo)

    expected_export = json.dumps(
        lesson_to_export(Lesson.model_validate(_real_internal_lesson())),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    assert first == {"created": 1, "unchanged": 0, "updated": 0}
    assert second == {"created": 0, "unchanged": 1, "updated": 0}
    assert (repo / "dist" / "lessons" / "ordinal_numbers.json").read_text(encoding="utf-8") == (
        expected_export
    )
