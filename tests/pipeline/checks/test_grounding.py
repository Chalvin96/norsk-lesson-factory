"""Tests for grounding target-form extraction + the report-only diagnostic."""

from __future__ import annotations

from lesson_builder.pipeline.checks.defect_rules import (
    exercise_target_forms,
    grounding_gap_count,
    taught_surface,
)


def test_taught_surface_collects_paragraph_and_example_tokens():
    lesson = {
        "elements": [
            {
                "element_kind": "section",
                "blocks": [
                    {"kind": "paragraph", "spans": [{"kind": "text", "value": "Jeg kan snakke norsk."}]},
                    {"kind": "example", "no": [{"kind": "text", "value": "Hun leser boka."}], "en": []},
                ],
            }
        ]
    }
    surface = taught_surface(lesson)
    assert "snakke" in surface
    assert "leser" in surface


def test_exercise_target_forms_build_uses_nonfixed_tokens():
    ex = {
        "element_kind": "exercise",
        "operation": "build",
        "payload": {"tokens": [
            {"token_id": "t1", "text": "snakke", "fixed": False},
            {"token_id": "t2", "text": ".", "fixed": True},
        ]},
    }
    assert "snakke" in exercise_target_forms(ex)


def test_exercise_target_forms_choose_skips_plainly_english_option():
    ex = {
        "element_kind": "exercise",
        "operation": "choose",
        "payload": {"answer_id": "a", "options": [{"option_id": "a", "text": "I can come."}]},
    }
    assert exercise_target_forms(ex) == set()


def test_grounding_gap_count_flags_untaught_form():
    lesson = {
        "elements": [
            {"element_kind": "section", "blocks": [
                {"kind": "paragraph", "spans": [{"kind": "text", "value": "Vi lærer modalverb."}]}
            ]},
            {"element_kind": "exercise", "operation": "build", "payload": {"tokens": [
                {"token_id": "t1", "text": "kylling", "fixed": False},
            ]}},
        ]
    }
    assert grounding_gap_count(lesson) == 1


def test_grounding_gap_count_grounded_form_not_flagged():
    lesson = {
        "elements": [
            {"element_kind": "section", "blocks": [
                {"kind": "paragraph", "spans": [{"kind": "text", "value": "Jeg kan snakke norsk."}]}
            ]},
            {"element_kind": "exercise", "operation": "build", "payload": {"tokens": [
                {"token_id": "t1", "text": "snakke", "fixed": False},
            ]}},
        ]
    }
    assert grounding_gap_count(lesson) == 0
