"""Entry point: prevention coverage across authored lesson projections."""

from __future__ import annotations

from typing import Any

from lesson_builder.application.operations.audit_source import audit_source_text
from lesson_builder.application.operations.load_lesson import load_lesson_source
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions


def test_authoring_projections_given_positive_and_labeled_negative_examples_expect_visible_content_and_safe_audio():
    lesson = load_lesson_source(K_EXAMPLE_LESSON, K_CHOOSE_EXERCISE)
    internal = lesson.model_dump(mode="json")

    exported = to_export_dict(lesson)
    transcript = derive_transcript_blocks(internal)
    attempt = extract_attempt_questions(internal)
    answer_review = extract_answer_questions(internal)

    visible_examples = exported["sections"][0]["blocks"][0]["items"]
    assert [_text(item["no"]) for item in visible_examples] == [
        "Jeg kommer.",
        "Incorrect: Jeg komme.",
        "Not: Jeg kommer. (in this situation)",
    ]
    assert [item["text"] for item in transcript] == ["Jeg kommer."]
    assert attempt[0]["id"] == "choose-example"
    assert attempt[0]["operation"] == "choose"
    assert attempt[0]["prompt"] == "Choose the natural sentence."
    assert attempt[0]["stem"] == "Which sentence is natural?"
    assert [option["text"] for option in attempt[0]["options"]] == ["Jeg kommer.", "Jeg komme."]
    assert all(option["option_id"].startswith("review-option-") for option in attempt[0]["options"])
    assert answer_review[0]["lesson_context"][0]["blocks"][0]["items"] == [
        {"no": _text(item["no"]), "en": _text(item["en"])} for item in visible_examples
    ]


def test_source_audit_given_outer_ascii_quote_wrappers_expect_blocking_but_intentional_quotes_allowed():
    wrapped = audit_source_text(K_CHOOSE_EXERCISE, lesson_text=_example_lesson('"Jeg kommer."', '"I am coming."'))

    assert wrapped.status == "blocked"
    assert [(finding.code, finding.severity) for finding in wrapped.findings] == [
        ("typed-example-outer-quote-wrapper", "blocking")
    ]

    intentional = audit_source_text(
        K_CHOOSE_EXERCISE,
        lesson_text=_example_lesson(
            'Læreren sa: "Jeg kommer."\n- en: The teacher said: "I am coming."\n- no: Olas bok\n- en: Ola has a book\n- no: «Jeg kommer.»',
            "«I am coming.»",
        ),
    )

    assert not any(finding.code == "typed-example-outer-quote-wrapper" for finding in intentional.findings)


def test_recall_projection_given_keyed_sentence_expect_audio_target_and_visible_boundaries_match():
    lesson = load_lesson_source(K_RECALL_LESSON, _recall_exercise())
    internal = lesson.model_dump(mode="json")

    exported = to_export_dict(lesson)
    payload = internal["elements"][1]["payload"]

    assert _keyed_recall_text(payload) == "På fredag kommer vi hjem."
    assert payload["audio_target"] == _keyed_recall_text(payload)
    assert _text(exported["exercises"][0]["payload"]["segments"][0]["spans"]) == "På fredag "
    assert _text(exported["exercises"][0]["payload"]["segments"][2]["spans"]) == " vi hjem."
    assert [item["text"] for item in derive_transcript_blocks(internal)] == ["På fredag kommer vi hjem."]


def test_recall_source_audit_given_missing_word_boundary_expect_blocking_and_punctuation_suffix_allowed():
    blocked = audit_source_text(_recall_exercise(suffix=".Jonas"), lesson_text=K_RECALL_LESSON)

    assert blocked.status == "blocked"
    assert any(finding.code == "recall-rendered-boundary-missing" for finding in blocked.findings)

    allowed = audit_source_text(_recall_exercise(suffix="."), lesson_text=K_RECALL_LESSON)

    assert allowed.status == "clean"
    assert not any(finding.code == "recall-rendered-boundary-missing" for finding in allowed.findings)


def _example_lesson(first_no: str, first_en: str) -> str:
    """Return one compact authored lesson whose example block is under test."""
    return K_EXAMPLE_LESSON.replace("Jeg kommer.", first_no, 1).replace("I am coming.", first_en, 1)


def _recall_exercise(*, suffix: str = " vi hjem.") -> str:
    """Return a recall source with a test-controlled boundary after its blank."""
    return K_RECALL_EXERCISE.replace(" vi hjem.", suffix)


def _keyed_recall_text(payload: dict[str, Any]) -> str:
    """Reconstruct the learner sentence from keyed recall segments."""
    return "".join(
        _text(segment["spans"]) if segment["kind"] == "span" else segment["options"][segment["answer_index"]]
        for segment in payload["segments"]
    )


def _text(spans: list[dict[str, Any]]) -> str:
    """Flatten source or export spans without adding formatting artifacts."""
    return "".join(
        _text(span["children"]) if span["kind"] == "sentence" else str(span.get("value", "")) for span in spans
    )


K_EXAMPLE_LESSON = """---
type: Lesson
slug: prevention_projection
title: Projection prevention
cefr_level: A1
goal: Keep examples and exercise context aligned.
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand]
objectives:
  - id: obj-example
    statement: Recognise the natural example.
    bloom_targets: [understand]
requirements_ref: tests/fixtures/lesson_packages/doctor_appointment/grammar/brief.md
---

## Model examples {#sec-model role=model objectives=obj-example}

::: examples
- no: Jeg kommer.
- en: I am coming.
- no: Incorrect: *Jeg komme.*
- en: Incorrect present-tense form.
- no: Not: *Jeg kommer.* (in this situation)
- en: Not in this situation: use a question.
:::

{{exercise: choose-example}}

## Recap {#sec-recap role=recap}

Remember the natural example.
"""

K_CHOOSE_EXERCISE = """- handle: choose-example
  op: choose
  objective: obj-example
  bloom: understand
  prompt_md: Choose the natural sentence.
  stem_md: Which sentence is natural?
  options:
    - id: natural
      text: Jeg kommer.
      correct: true
      why: This uses the present-tense form.
    - id: incorrect
      text: Jeg komme.
      correct: false
      why: This form is not natural here.
  derived_from:
    - section_id: sec-model
"""

K_RECALL_LESSON = """---
type: Lesson
slug: recall_projection
title: Recall projection
cefr_level: A1
goal: Recall one sentence.
default_lang: nb
grounding_mode: grounded
bloom_targets: [apply]
objectives:
  - id: obj-recall
    statement: Recall the sentence.
    bloom_targets: [apply]
requirements_ref: tests/fixtures/lesson_packages/doctor_appointment/grammar/brief.md
---

## Model {#sec-model role=model objectives=obj-recall}

På fredag kommer vi hjem.

{{exercise: recall-sentence}}

## Recap {#sec-recap role=recap}

Remember the sentence.
"""

K_RECALL_EXERCISE = """- handle: recall-sentence
  op: recall_fill
  objective: obj-recall
  bloom: apply
  prompt_md: Complete the sentence.
  audio_target: På fredag kommer vi hjem.
  segments:
    - text_md: "På fredag "
    - blank_id: verb
      options: [kommer, kom]
      answer_index: 0
    - text_md: " vi hjem."
  derived_from:
    - section_id: sec-model
"""
