"""Tests for the lesson index: hydration, idempotency, retrieval."""

from __future__ import annotations

import json
from pathlib import Path

from lesson_builder.pipeline.store.index import LessonIndex, hydrate

ROOT = Path(__file__).resolve().parents[3]


def _write_lesson(tmp_path: Path, slug: str, lesson: dict) -> Path:
    lessons = tmp_path / "lessons"
    lessons.mkdir(exist_ok=True)
    path = lessons / f"{slug}.json"
    path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2))
    return path


def _simple_lesson(slug: str) -> dict:
    return {
        "key": slug,
        "concept_slug": slug,
        "grounding_mode": "authored_golden",
        "title": f"Lesson {slug}",
        "cefr_level": "A1",
        "goal": "Learn",
        "objectives": [{"id": "o1", "statement": "learn word order", "bloom_targets": ["understand"]}],
        "elements": [
            {
                "element_kind": "section",
                "id": "s1",
                "role": "orient",
                "objective_ids": ["o1"],
                "title": "Intro",
                "blocks": [{"kind": "prose", "spans": [{"kind": "text", "value": "Word order matters."}]}],
            },
            {
                "element_kind": "exercise",
                "id": "ex1",
                "operation": "judge",
                "objective_id": "o1",
                "bloom_level": "understand",
                "derived_from": [],
                "prompt": [{"kind": "text", "value": "Is this correct word order?"}],
                "explanation": "ex",
                "payload": {"sentence": [{"kind": "text", "value": "Test"}], "is_correct": True, "feedback": "f"},
            },
        ],
        "review_pool": {
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [{"uuid": "00000000-0000-0000-0000-000000000001", "exercise_id": "ex1"}],
                }
            ]
        },
    }


def test_hydrate_given_fresh_lessons_expect_created(tmp_path: Path):
    _write_lesson(tmp_path, "test_lesson", _simple_lesson("test_lesson"))
    db = tmp_path / "index.db"
    result = hydrate(tmp_path / "lessons", db_path=db)
    assert result == {"created": 1, "unchanged": 0, "updated": 0, "deleted": 0}


def test_hydrate_given_unchanged_lessons_expect_unchanged(tmp_path: Path):
    _write_lesson(tmp_path, "test_lesson", _simple_lesson("test_lesson"))
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)
    result = hydrate(tmp_path / "lessons", db_path=db)
    assert result == {"created": 0, "unchanged": 1, "updated": 0, "deleted": 0}


def test_hydrate_given_modified_lesson_expect_updated(tmp_path: Path):
    lesson = _simple_lesson("test_lesson")
    _write_lesson(tmp_path, "test_lesson", lesson)
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)
    lesson["title"] = "Changed Title"
    _write_lesson(tmp_path, "test_lesson", lesson)
    result = hydrate(tmp_path / "lessons", db_path=db)
    assert result == {"created": 0, "unchanged": 0, "updated": 1, "deleted": 0}


def test_hydrate_given_removed_lesson_file_expect_stale_slug_deleted(tmp_path: Path):
    lesson_path = _write_lesson(tmp_path, "test_lesson", _simple_lesson("test_lesson"))
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)
    idx = LessonIndex(db)
    assert idx.slugs() == ["test_lesson"]
    idx.close()

    lesson_path.unlink()
    result = hydrate(tmp_path / "lessons", db_path=db)

    assert result["deleted"] == 1
    idx = LessonIndex(db)
    try:
        assert idx.slugs() == []
    finally:
        idx.close()


def test_insert_unit_given_hydrate_expect_file_hash_column_is_real_content_hash(tmp_path: Path):
    _write_lesson(tmp_path, "test_lesson", _simple_lesson("test_lesson"))
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)

    idx = LessonIndex(db)
    try:
        rows = idx._conn.execute(
            "SELECT DISTINCT file_hash FROM lesson_units WHERE slug = ?", ("test_lesson",)
        ).fetchall()
        recorded_hash = idx._conn.execute(
            "SELECT file_hash FROM file_hashes WHERE slug = ?", ("test_lesson",)
        ).fetchone()[0]
    finally:
        idx.close()

    # lesson_units.file_hash must be the real content hash (matches file_hashes),
    # not the slug string.
    assert {r[0] for r in rows} == {recorded_hash}
    assert recorded_hash != "test_lesson"


def test_slugs_given_indexed_lessons_expect_all(tmp_path: Path):
    _write_lesson(tmp_path, "a", _simple_lesson("a"))
    _write_lesson(tmp_path, "b", _simple_lesson("b"))
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)
    idx = LessonIndex(db)
    try:
        assert sorted(idx.slugs()) == ["a", "b"]
    finally:
        idx.close()


def test_units_for_slug_given_lesson_expect_all_unit_types(tmp_path: Path):
    _write_lesson(tmp_path, "test_lesson", _simple_lesson("test_lesson"))
    db = tmp_path / "index.db"
    hydrate(tmp_path / "lessons", db_path=db)
    idx = LessonIndex(db)
    try:
        units = idx.units_for_slug("test_lesson")
        types = {u["unit_type"] for u in units}
        assert "objective" in types
        assert "exercise" in types
    finally:
        idx.close()
