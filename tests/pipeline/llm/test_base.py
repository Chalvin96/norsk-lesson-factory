import pytest

import lesson_builder.pipeline.llm.base as base_mod


def test_extract_json_object_given_markdown_fenced_json_expect_stripped_dict():
    assert base_mod.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_object_given_surrounding_text_expect_repaired_object():
    assert base_mod.extract_json_object('prefix text {"a": 1} trailing') == {"a": 1}


def test_extract_json_object_given_no_json_expect_value_error():
    with pytest.raises(ValueError):
        base_mod.extract_json_object("no json here")


def test_extract_json_object_given_trailing_text_expect_repaired_object():
    assert base_mod.extract_json_object('{"a": 1}\nNote: extra text') == {"a": 1}


def test_extract_json_object_given_multiple_objects_expect_value_error():
    with pytest.raises(ValueError):
        base_mod.extract_json_object('{"first": true} {"second": true}')


def test_extract_json_object_given_nested_braces_expect_object_returned():
    assert base_mod.extract_json_object('{"a": {"nested": 1}}') == {"a": {"nested": 1}}


def test_extract_json_object_given_unquoted_key_expect_repaired_object():
    assert base_mod.extract_json_object("{a: 1}") == {"a": 1}


def test_matches_quota_given_matching_pattern_expect_true():
    class _Client(base_mod.BaseLlmClient):
        name = "t"
        quota_patterns = ("rate limit",)

        def call(self, prompt, *, model=None):
            return ""

    assert _Client().matches_quota("Rate Limit exceeded") is True


def test_matches_quota_given_no_matching_pattern_expect_false():
    class _Client(base_mod.BaseLlmClient):
        name = "t"
        quota_patterns = ("rate limit",)

        def call(self, prompt, *, model=None):
            return ""

    assert _Client().matches_quota("all good") is False
