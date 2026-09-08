"""Tests for raw and typed Markdown front-matter contracts."""

from __future__ import annotations

import pytest

from lesson_builder.formats.markdown.frontmatter import parse_frontmatter
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter


def test_parse_source_frontmatter_given_raw_plan_with_leading_body_newlines_expect_exact_suffix():
    metadata, body = parse_source_frontmatter("---\ntitle: Plan\ntitle: Final\n---\n\n# Body\n")

    assert metadata == {"title": "Final"}
    assert body == "\n\n# Body\n"


def test_parse_source_frontmatter_given_non_mapping_yaml_expect_type_error():
    with pytest.raises(TypeError, match="mapping"):
        parse_source_frontmatter("---\n- one\n- two\n---\nbody")


def test_parse_source_frontmatter_given_missing_delimiter_expect_value_error():
    with pytest.raises(ValueError, match="unterminated"):
        parse_source_frontmatter("---\ntitle: Plan\nbody")


def test_parse_frontmatter_given_duplicate_key_expect_strict_value_error():
    with pytest.raises(ValueError, match="duplicate YAML key"):
        parse_frontmatter(
            """---
type: Lesson
slug: sample
title: First
title: Second
cefr_level: A1
goal: Goal
default_lang: nb
grounding_mode: grounded
bloom_targets: [remember]
objectives:
  - id: obj-sample
    statement: State the goal.
    bloom_targets: [remember]
requirements_ref: plan.md
---
## model
"""
        )
