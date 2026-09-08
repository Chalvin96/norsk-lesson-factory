"""Tests for the canonical lesson-slug contract."""

from __future__ import annotations

import pytest

from lesson_builder.domain.lesson.validation.lesson_ids import InvalidSlugError
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug


def test_validate_slug_given_single_component_expect_returned_unchanged():
    assert validate_slug("adjective_agreement") == "adjective_agreement"


def test_validate_slug_given_digit_component_expect_returned_unchanged():
    assert validate_slug("ordinal_numbers_2") == "ordinal_numbers_2"


def test_validate_slug_given_parent_traversal_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("../../etc/passwd")


def test_validate_slug_given_path_separator_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("nested/slug")


def test_validate_slug_given_empty_string_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("")


def test_validate_slug_given_uppercase_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("Adjective_Agreement")


def test_validate_slug_given_leading_underscore_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("_hidden")


def test_validate_slug_given_dotted_name_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug("lesson.json")


def test_validate_slug_given_non_string_expect_invalid_slug_error():
    with pytest.raises(InvalidSlugError):
        validate_slug(None)  # type: ignore[arg-type]
