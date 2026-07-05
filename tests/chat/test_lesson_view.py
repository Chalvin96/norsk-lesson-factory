"""Behavioral tests for the pure lesson renderer."""

from __future__ import annotations

from lesson_builder.chat.lesson_view import render_lesson


def test_render_lesson_given_metadata_and_section_expect_header_and_preview():
    # setup
    lesson = {
        "key": "demo",
        "title": "Demo lesson",
        "cefr_level": "A1",
        "goal": "Do the thing",
        "objectives": [{"id": "obj_1", "statement": "Know the thing"}],
        "elements": [
            {
                "element_kind": "section",
                "id": "sec_0",
                "role": "orient",
                "title": "Intro",
                "blocks": [
                    {"kind": "paragraph", "spans": [{"kind": "text", "value": "Hello there."}]}
                ],
            }
        ],
    }

    # execute
    lines = render_lesson(lesson)

    # assert
    text = "\n".join(lines)
    assert "demo — Demo lesson (A1)" in lines[0]
    assert "Goal: Do the thing" in text
    assert "obj_1: Know the thing" in text
    assert "[sec_0]  section · orient" in text
    assert "Hello there." in text


def test_render_lesson_given_choose_and_recall_fill_expect_answer_lines():
    # setup
    lesson = {
        "key": "demo",
        "title": "Demo",
        "cefr_level": "A1",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_choose",
                "operation": "choose",
                "bloom_level": "understand",
                "prompt": [{"kind": "text", "value": "Pick one."}],
                "payload": {
                    "answer_id": "b",
                    "options": [
                        {"option_id": "a", "text": "wrong"},
                        {"option_id": "b", "text": "right"},
                    ],
                },
            },
            {
                "element_kind": "exercise",
                "id": "ex_fill",
                "operation": "recall_fill",
                "bloom_level": "understand",
                "prompt": [{"kind": "text", "value": "Fill it."}],
                "payload": {
                    "segments": [
                        {"kind": "span", "spans": [{"kind": "text", "value": "et "}]},
                        {"kind": "blank", "options": ["fin", "fint", "fine"], "answer_index": 1},
                    ]
                },
            },
        ],
    }

    # execute
    text = "\n".join(render_lesson(lesson))

    # assert
    assert "[ex_choose]  exercise · choose  (understand)" in text
    assert "✓ right" in text
    assert "✓ et [fint]" in text
