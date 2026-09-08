"""Regression tests for rich-authoring prompt contracts."""

from __future__ import annotations

from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_review_prompt


def test_normalization_prompt_given_negative_example_expect_inline_marker_required() -> None:
    prompt = build_normalization_prompt("plan", "draft", "[]", plan_kind="lesson")

    assert "inline compiler-recognized" in prompt
    assert "- no: <bad Norwegian>" in prompt
    assert "- en: <English gloss>" in prompt
    assert "- en: Incorrect: <English gloss>" in prompt
    assert "- en: 'Incorrect: <English gloss>'" not in prompt
    assert "do not add YAML-style outer quote characters" in prompt
    assert "learner-visible punctuation" in prompt
    assert "Never group multiple `no:` items" in prompt
    assert "preceding heading" in prompt
    assert "keep the Norwegian `no:` bytes unchanged" in prompt


def test_preservation_prompt_given_lost_negative_status_expect_semantic_defect() -> None:
    prompt = build_preservation_review_prompt(
        plan_text="plan",
        draft_text="draft",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "inline compiler-recognized negative marker" in prompt
    assert "surrounding heading" in prompt
    assert "needs_repair" in prompt
