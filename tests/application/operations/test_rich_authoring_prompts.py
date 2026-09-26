"""Regression tests for rich-authoring prompt contracts."""

from __future__ import annotations

from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_author_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_rich_draft_prompt


def test_rich_draft_prompt_given_non_grammar_plan_expect_primary_goal_and_separability_rule() -> None:
    prompt = build_rich_draft_prompt("plan", plan_kind="communicative")

    assert "one primary scored evidence goal" in prompt
    assert "one cohesive learner submission" in prompt
    assert "attempted, omitted, judged, and remediated independently" in prompt
    assert "word, item, or criterion counts are not the rule" in prompt


def test_rich_draft_prompt_given_grammar_plan_expect_primary_goal_rule_not_duplicated() -> None:
    prompt = build_rich_draft_prompt("plan", plan_kind="grammar")

    assert prompt.count("one primary scored evidence goal") == 1
    assert prompt.count("{{checkpoint") == 1
    assert "stable_handle" in prompt


def test_lesson_review_prompt_given_compound_checkpoint_expect_practice_gap_distinction() -> None:
    prompt = build_lesson_review_prompt("plan", "draft", plan_kind="communicative")

    assert "`practice_gap` finding with major severity" in prompt
    assert "independently attemptable, omittable, judgeable, and remediable outputs" in prompt
    assert "operation-native homogeneous" in prompt
    assert "one submission, not" in prompt


def test_normalization_prompt_given_negative_example_expect_typed_role_required() -> None:
    prompt = build_normalization_prompt("plan", "draft", "[]", plan_kind="lesson")

    assert "teaching_role=incorrect" in prompt
    assert "teaching_role=caution" in prompt
    assert "- en: <English gloss>" in prompt
    assert "Do not add `✗`" in prompt
    assert "do not add YAML-style outer quote characters" in prompt
    assert "learner-visible punctuation" in prompt
    assert "Never group multiple `no:` items" in prompt
    assert "keep the Norwegian `no:` bytes unchanged" not in prompt


def test_normalization_prompt_given_mechanical_contract_expect_parser_owned_rules_absent() -> None:
    prompt = build_normalization_prompt("plan", "draft", "[]", plan_kind="lesson")

    assert "in that order" not in prompt
    assert "Quote every YAML scalar" not in prompt
    assert "Evidence-route registry" in prompt


def test_normalization_prompt_given_route_selection_expect_one_selection_vs_pair_matching() -> None:
    prompt = build_normalization_prompt("plan", "draft", "[]", plan_kind="lesson")

    assert "`meaning_selection` names one primary selection" in prompt
    assert "is `pair_matching`" in prompt
    assert "do not collapse such a set into one selection route" in prompt


def test_exercise_author_prompt_given_composite_choice_risk_expect_bundled_mapping_forbidden() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "one scored decision" in prompt
    assert "competing answers to that same question" in prompt
    assert (
        "not route a task to `meaning_selection` when every choice would repeat a complete multi-part mapping" in prompt
    )
    assert "several `x = y` mappings" in prompt
    assert "semicolon-delimited independent claims" in prompt
    assert "multiple separately judgeable propositions" in prompt


def test_exercise_author_prompt_given_verbose_prompt_risk_expect_level_appropriate_textbook_directions() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "concise textbook or Norskprøve tasks" in prompt
    assert "A1 and A2 task directions in prompt_md, stem_md, and per-item cues use concise English" in prompt
    assert "B1 and higher task directions use natural Bokmål" in prompt
    assert "never restate the taught rule table" in prompt.lower()
    assert "belong in criteria, judge_prompt, and feedback, never in prompt_md or stem_md" in prompt


def test_exercise_author_prompt_given_route_authority_expect_match_pairs_gated_on_route() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "Use `match_pairs` only when the request's" in prompt
    assert "`pair_matching`" in prompt
    assert "fail the stage rather than disguising a compound task" in prompt
    assert "one request -> one exercise identity" in prompt
    assert "Use `recall_fill` or `judge` only when the request's authoritative route permits" in prompt


def test_exercise_author_prompt_given_removed_instruction_expect_complete_mapping_encoding_absent() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "encode the complete interpretations as options" not in prompt
    assert "multiple interpretation parts" not in prompt
    assert "top-level YAML list items have" not in prompt
    assert "Concrete operation payload shapes" in prompt


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
