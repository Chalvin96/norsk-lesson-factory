"""Tests for ``scripts/check_lessons.py``: the per-lesson gate + dist-verify check.

Tests the core ``check_lesson`` function directly (no git, no real dist dir).
Uses a real corpus lesson (``past_tense``, which passes the gate with zero
blocking results) as the fixture, writing dist files into ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.schema import Lesson

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_lessons import LessonCheckResult, check_lesson, check_lesson_at  # noqa: E402


def _load_lesson(slug: str = "past_tense") -> dict[str, Any]:
    return json.loads((REPO_ROOT / "data" / "lessons" / f"{slug}.json").read_text(encoding="utf-8"))


def _fresh_dist_text(lesson: dict[str, Any]) -> str:
    export = lesson_to_export(Lesson.model_validate(lesson))
    return json.dumps(export, ensure_ascii=False, indent=2) + "\n"


def test_check_lesson_given_passing_gate_and_fresh_dist_expect_clean():
    # setup: a real lesson that passes the gate with zero blocking findings,
    # paired with its freshly computed dist text.
    lesson = _load_lesson("past_tense")
    dist_text = _fresh_dist_text(lesson)

    # execute
    result = check_lesson(lesson, dist_text)

    # assert
    assert result.is_clean
    assert result.blocking == []
    assert result.dist_detail is None


def test_check_lesson_given_stale_dist_expect_stale_failure():
    # setup: same passing lesson, but the dist text is mutated so it no longer
    # matches the freshly computed export.
    lesson = _load_lesson("past_tense")
    stale_export = lesson_to_export(Lesson.model_validate(lesson))
    stale_export["title"] = stale_export["title"] + " [mutated]"
    dist_text = json.dumps(stale_export, ensure_ascii=False, indent=2) + "\n"

    # execute
    result = check_lesson(lesson, dist_text)

    # assert
    assert not result.is_clean
    assert result.blocking == []
    assert result.dist_detail == "stale"


def test_check_lesson_given_missing_dist_expect_missing_failure():
    # setup: a lesson whose dist file does not exist (dist_text=None).
    lesson = _load_lesson("past_tense")

    # execute
    result = check_lesson(lesson, None)

    # assert
    assert not result.is_clean
    assert result.dist_detail == "missing"


def test_check_lesson_at_given_fresh_dist_on_disk_expect_clean(tmp_path: Path):
    # setup: write the lesson + fresh dist into tmp_path, then check via the
    # file-based wrapper.
    lesson = _load_lesson("past_tense")
    lesson_path = tmp_path / "past_tense.json"
    dist_path = tmp_path / "past_tense.dist.json"
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False), encoding="utf-8")
    dist_path.write_text(_fresh_dist_text(lesson), encoding="utf-8")

    # execute
    result = check_lesson_at(lesson_path, dist_path)

    # assert
    assert result.is_clean
    assert isinstance(result, LessonCheckResult)


def test_check_lesson_at_given_no_dist_file_expect_missing_failure(tmp_path: Path):
    # setup: lesson exists but the dist path does not.
    lesson = _load_lesson("past_tense")
    lesson_path = tmp_path / "past_tense.json"
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False), encoding="utf-8")
    dist_path = tmp_path / "nonexistent.json"

    # execute
    result = check_lesson_at(lesson_path, dist_path)

    # assert
    assert not result.is_clean
    assert result.dist_detail == "missing"
