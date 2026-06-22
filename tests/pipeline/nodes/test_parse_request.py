"""Tests for parse_request: deterministic keyword-based parsing."""

from __future__ import annotations

from lesson_builder.pipeline.nodes.parse_request import parse_request

_KNOWN = ["word_order_main_clauses", "past_tense", "collocations"]


def test_parse_request_given_add_exercises_with_slug_expect_add_exercises_operation():
    spec = parse_request(
        "add 2 exercises on fordi for word_order_main_clauses", known_slugs=_KNOWN
    )
    assert spec.operation == "add_exercises"
    assert spec.target_slug == "word_order_main_clauses"
    assert spec.count == 2
    assert spec.target_content == "fordi"


def test_parse_request_given_add_explanation_expect_add_explanation_operation():
    spec = parse_request("add more explanation about past tense", known_slugs=_KNOWN)
    assert spec.operation == "add_explanation"
    assert spec.target_slug == "past_tense"


def test_parse_request_given_improve_keyword_expect_improve_operation():
    spec = parse_request("improve the collocations lesson", known_slugs=_KNOWN)
    assert spec.operation == "improve"
    assert spec.target_slug == "collocations"


def test_parse_request_given_unknown_slug_expect_low_confidence():
    spec = parse_request("add exercises on something_new", known_slugs=_KNOWN)
    assert spec.target_slug is None
    assert spec.confidence == "low"


def test_parse_request_given_bloom_level_expect_extracted():
    spec = parse_request(
        "add 1 analyze exercise for past_tense", known_slugs=_KNOWN
    )
    assert spec.bloom_level == "analyze"


def test_parse_request_given_word_number_expect_count():
    spec = parse_request("add three exercises for collocations", known_slugs=_KNOWN)
    assert spec.count == 3


def test_parse_request_given_slug_alias_expect_best_matching_known_slug():
    spec = parse_request(
        "add 2 harder exercises on fordi for the word-order lesson",
        known_slugs=["word_order_main_clauses", "question_word_order"],
    )
    assert spec.target_slug == "word_order_main_clauses"
