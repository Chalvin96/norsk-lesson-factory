"""Entry point: tests for the text-level source loader (`load_lesson_source`).

Covers source-text loading (fixture → valid Lesson), byte-stable double load,
exercise-marker extraction, and missing-recap errors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.domain.lesson.models.lesson import Lesson
from tests.application.operations.utils import K_TEST_EXERCISES_SOURCE
from tests.application.operations.utils import K_TEST_LESSON_SOURCE
from tests.application.operations.utils import load_lesson_files
from tests.application.operations.utils import write_lesson_source

# ── End-to-end load ──────────────────────────────────────────────────────


def test_load_lesson_source_given_demo_fixture_expect_valid_lesson(tmp_path: Path):
    src = write_lesson_source(tmp_path)
    lesson = load_lesson_files(src)

    assert isinstance(lesson, Lesson)
    assert lesson.key == "demo"
    assert lesson.title == "Demo Lesson"

    sections = [e for e in lesson.elements if e.element_kind == "section"]
    exercises = [e for e in lesson.elements if e.element_kind == "exercise"]
    assert len(sections) >= 2
    assert len(exercises) >= 2
    section_roles = {s.role for s in sections}
    assert "orient" in section_roles
    assert "model" in section_roles
    assert "recap" in section_roles

    assert len(lesson.review_pool.pools) >= 1
    pool = lesson.review_pool.pools[0]
    assert pool.key == "obj_greet"
    assert len(pool.cards) >= 2

    for card in pool.cards:
        assert card.exercise_id in {"ex-greet", "ex-choose"}


def test_load_lesson_source_given_explicit_heading_role_attribute_expect_clean_section_titles(
    tmp_path: Path,
):
    lesson_md = (
        K_TEST_LESSON_SOURCE.replace(
            "## orient: Introduction {#sec-intro}",
            "## What you will learn in this lesson {#sec-intro role=orient}",
        )
        .replace(
            "## model: Greetings {#sec-model objectives=obj_greet}",
            "## Greetings {#sec-model objectives=obj_greet role=model}",
        )
        .replace(
            "## recap: Summary {#sec-recap}",
            "## Summary {#sec-recap role=recap}",
        )
    )
    source = write_lesson_source(tmp_path, lesson_text=lesson_md)

    lesson = load_lesson_files(source)

    sections = [element for element in lesson.elements if element.element_kind == "section"]
    assert [section.title for section in sections] == [
        "What you will learn in this lesson",
        "Greetings",
        "Summary",
    ]
    assert [section.role for section in sections] == ["orient", "model", "recap"]


def test_load_lesson_source_given_demo_fixture_expect_interleaved_order(tmp_path: Path):
    src = write_lesson_source(tmp_path)
    lesson = load_lesson_files(src)

    kinds = [e.element_kind for e in lesson.elements]
    # Elements should be: section, exercise, section, exercise, section
    assert kinds[0] == "section"
    assert kinds[1] == "exercise"
    assert kinds[2] == "section"
    assert kinds[3] == "exercise"
    assert kinds[4] == "section"


def test_load_lesson_source_given_demo_fixture_expect_card_uuids_deterministic(tmp_path: Path):
    src = write_lesson_source(tmp_path)
    first = load_lesson_files(src)
    second = load_lesson_files(src)

    assert first.review_pool == second.review_pool
    assert all(card.uuid.version == 5 for pool in first.review_pool.pools for card in pool.cards)


# ── Determinism: byte-identical double load ──────────────────────────────


def test_load_lesson_source_given_double_load_expect_byte_identical(tmp_path: Path):
    src1 = write_lesson_source(tmp_path / "run1")
    src2 = write_lesson_source(tmp_path / "run2")

    dict1 = load_lesson_files(src1).model_dump(mode="json")
    dict2 = load_lesson_files(src2).model_dump(mode="json")

    json1 = json.dumps(dict1, sort_keys=True, ensure_ascii=False)
    json2 = json.dumps(dict2, sort_keys=True, ensure_ascii=False)
    assert json1 == json2


def test_load_lesson_source_given_reload_same_dir_expect_byte_identical(
    tmp_path: Path,
):
    src = write_lesson_source(tmp_path)
    dict1 = load_lesson_files(src).model_dump(mode="json")
    dict2 = load_lesson_files(src).model_dump(mode="json")

    json1 = json.dumps(dict1, sort_keys=True, ensure_ascii=False)
    json2 = json.dumps(dict2, sort_keys=True, ensure_ascii=False)
    assert json1 == json2


# ── Exercise marker extraction ───────────────────────────────────────────


def test_load_lesson_source_given_exercise_marker_expect_exercise_element(tmp_path: Path):
    src = write_lesson_source(tmp_path)
    lesson = load_lesson_files(src)

    exercises = [e for e in lesson.elements if e.element_kind == "exercise"]
    assert len(exercises) == 2
    # Ensure they are exercises, not paragraph blocks with marker text.
    assert exercises[0].operation == "judge"
    assert exercises[1].operation == "choose"


def test_load_lesson_source_given_inline_exercise_marker_expect_value_error(
    tmp_path: Path,
):
    bad_md = K_TEST_LESSON_SOURCE.replace(
        "Hei! Velkommen til norsk.\n",
        "Text with {{exercise: ex-greet}} inline.\n",
    )
    src = write_lesson_source(tmp_path, lesson_text=bad_md)
    with pytest.raises(ValueError, match="inline/partial marker"):
        load_lesson_files(src)


def test_load_lesson_source_given_underscore_exercise_marker_expect_exercise_element(
    tmp_path: Path,
):
    lesson_md = K_TEST_LESSON_SOURCE.replace("ex-greet", "choose_definite_plural")
    exercises_yaml = K_TEST_EXERCISES_SOURCE.replace("ex-greet", "choose_definite_plural")
    src = write_lesson_source(tmp_path, lesson_text=lesson_md, exercises_text=exercises_yaml)

    lesson = load_lesson_files(src)

    exercises = [element for element in lesson.elements if element.element_kind == "exercise"]
    assert [exercise.id for exercise in exercises] == [
        "choose_definite_plural",
        "ex-choose",
    ]


def test_load_lesson_source_given_marker_without_exercise_expect_value_error(tmp_path: Path):
    lesson_text = K_TEST_LESSON_SOURCE.replace("ex-greet", "missing-exercise", 1)
    source = write_lesson_source(tmp_path, lesson_text=lesson_text)

    with pytest.raises(ValueError, match="not found in exercises.yaml"):
        load_lesson_files(source)


def test_load_lesson_source_given_undeclared_exercise_objective_expect_value_error(
    tmp_path: Path,
):
    exercises_text = K_TEST_EXERCISES_SOURCE.replace(
        "objective: obj_greet",
        "objective: obj_missing",
        1,
    )
    source = write_lesson_source(tmp_path, exercises_text=exercises_text)

    with pytest.raises(ValueError, match="not declared in frontmatter"):
        load_lesson_files(source)


# ── Missing recap ────────────────────────────────────────────────────────


def test_load_lesson_source_given_missing_recap_expect_value_error(tmp_path: Path):
    no_recap_md = """\
---
type: Lesson
slug: demo
title: Demo Lesson
cefr_level: A1
goal: Learn basic Norwegian greetings.
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand]
objectives:
  - id: obj_greet
    statement: Greet someone in Norwegian.
    bloom_targets: [understand]
requirements_ref: curriculum/concepts/demo.md
---

## orient: Introduction {#sec-intro}

Hei!

## model: Greetings {#sec-model objectives=obj_greet}

[God morgen]{lang=nb} means good morning.
"""
    src = write_lesson_source(tmp_path, lesson_text=no_recap_md)
    with pytest.raises(ValueError, match="missing recap section"):
        load_lesson_files(src)


# ── Canonical dict shape ─────────────────────────────────────────────────


def test_load_lesson_source_given_demo_fixture_expect_canonical_dict_stable_keys(
    tmp_path: Path,
):
    src = write_lesson_source(tmp_path)
    lesson_dict = load_lesson_files(src).model_dump(mode="json")

    assert set(lesson_dict.keys()) == {
        "key",
        "concept_slug",
        "grounding_mode",
        "title",
        "cefr_level",
        "goal",
        "objectives",
        "elements",
        "review_pool",
    }
