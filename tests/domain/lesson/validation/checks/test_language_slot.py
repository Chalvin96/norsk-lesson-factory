"""Tests for ``language_slot_check`` and the ``looks_norwegian`` predicate."""

from __future__ import annotations

from lesson_builder.domain.lesson.validation.checks.defect_rules import looks_norwegian
from lesson_builder.domain.lesson.validation.checks.validators.language_slot import language_slot_check

# ---------------------------------------------------------------------------
# looks_norwegian predicate
# ---------------------------------------------------------------------------


def test_looks_norwegian_given_norwegian_prose_with_letter_and_function_words_expect_true():
    # "Dette er infinitiv, så det må stå med å." — has å and er/det/må/med/å
    assert looks_norwegian("Dette er infinitiv, så det må stå med å.")


def test_looks_norwegian_given_single_function_word_with_letter_expect_false():
    assert not looks_norwegian("Dette er viktig.")


def test_looks_norwegian_given_function_words_without_norwegian_letter_expect_false():
    # Has function words er/og but no æøå
    assert not looks_norwegian("Dette er present tense og passer ikke her.")


def test_looks_norwegian_given_quoted_norwegian_citation_expect_false():
    # Quoted segment removed, leaving English-only text
    assert not looks_norwegian("The word 'å lese' means to read.")


def test_looks_norwegian_given_quoted_citation_plus_bare_norwegian_prose_expect_true():
    # After removing the quote, the bare Norwegian outside still triggers
    assert looks_norwegian("Dette er infinitiv, 'så det' må stå med å.")


# ---------------------------------------------------------------------------
# language_slot_check — option "why" strings
# ---------------------------------------------------------------------------


def test_language_slot_check_given_norwegian_why_expect_one_advisory_warning():
    # setup
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_choose_1",
                "operation": "choose",
                "payload": {
                    "options": [
                        {
                            "option_id": "a",
                            "text": "å lese",
                            "why": "Dette er infinitiv, så det må stå med å.",
                        }
                    ],
                    "answer_id": "a",
                },
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert len(results) == 1
    result = results[0]
    assert result.check_id == "language_slot"
    assert result.severity == "warning"
    assert result.advisory
    assert result.unit_id == "ex_choose_1"
    assert not result.is_blocking


def test_language_slot_check_given_english_why_with_quoted_norwegian_expect_no_results():
    # setup
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_choose_2",
                "operation": "choose",
                "payload": {
                    "options": [
                        {
                            "option_id": "a",
                            "text": "å lese",
                            "why": "The word 'å lese' means to read.",
                        }
                    ],
                    "answer_id": "a",
                },
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert results == []


# ---------------------------------------------------------------------------
# language_slot_check — explanation text spans
# ---------------------------------------------------------------------------


def test_language_slot_check_given_norwegian_explanation_text_span_expect_flag():
    # setup
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_explain_1",
                "explanation": [{"kind": "text", "value": "Dette er infinitiv, så det må stå med å."}],
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert len(results) == 1
    assert results[0].unit_id == "ex_explain_1"


def test_language_slot_check_given_foreign_term_explanation_span_expect_no_results():
    # setup — foreign_term spans are legit Norwegian, must not be checked
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_explain_2",
                "explanation": [
                    {"kind": "foreign_term", "value": "Dette er infinitiv og det er riktig.", "lang": "no"}
                ],
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert results == []


# ---------------------------------------------------------------------------
# language_slot_check — example "en" span lists
# ---------------------------------------------------------------------------


def test_language_slot_check_given_norwegian_in_example_en_spans_expect_flag():
    # setup — the "en" (English) slot contains Norwegian metalanguage
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "section",
                "id": "sec_examples_1",
                "blocks": [
                    {
                        "kind": "example",
                        "no": [{"kind": "text", "value": "Jeg lesende en bok."}],
                        "en": [{"kind": "text", "value": "Dette er infinitiv, så det må stå med å."}],
                    }
                ],
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert len(results) == 1
    assert results[0].unit_id == "sec_examples_1"


def test_language_slot_check_given_english_example_en_spans_expect_no_results():
    # setup — the "en" slot is proper English
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "section",
                "id": "sec_examples_2",
                "blocks": [
                    {
                        "kind": "example",
                        "no": [{"kind": "text", "value": "Jeg leser en bok."}],
                        "en": [{"kind": "text", "value": "I am reading a book."}],
                    }
                ],
            }
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    assert results == []


# ---------------------------------------------------------------------------
# language_slot_check — mixed + empty edge cases
# ---------------------------------------------------------------------------


def test_language_slot_check_given_mixed_good_and_bad_exercises_expect_only_bad_flagged():
    # setup
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_clean",
                "explanation": [{"kind": "text", "value": "Bok is common-gender, so use the base form."}],
            },
            {
                "element_kind": "exercise",
                "id": "ex_bad",
                "explanation": [{"kind": "text", "value": "Dette er infinitiv, så det må stå med å."}],
            },
        ],
    }

    # execute
    results = language_slot_check(lesson)

    # assert
    bad = [r for r in results if r.unit_id == "ex_bad"]
    clean = [r for r in results if r.unit_id == "ex_clean"]
    assert len(bad) == 1
    assert clean == []


def test_language_slot_check_given_empty_lesson_expect_no_results():
    # execute
    results = language_slot_check({"concept_slug": "test_slug", "elements": []})

    # assert
    assert results == []
