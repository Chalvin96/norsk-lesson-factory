"""Regression tests for rich-authoring prompt contracts."""

from __future__ import annotations

from pathlib import Path

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


def test_lesson_review_prompt_given_language_concern_expect_unverified_advisory_only() -> None:
    prompt = build_lesson_review_prompt("plan", "draft", plan_kind="grammar")

    assert "an unverified AI suggestion" in prompt
    assert "an exact source excerpt as evidence" in prompt
    assert "Findings never authorize edits, require the author to apply them, or block approval" in prompt
    assert "lack of an example in this lesson is not evidence of a contradiction" in prompt
    assert "genuinely over-broad rule" in prompt


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

    assert "Each `choose` option must represent one scored decision" in prompt
    assert "do not bundle independently judgeable mappings or propositions into one option" in prompt


def test_exercise_author_prompt_given_verbose_prompt_risk_expect_level_appropriate_textbook_directions() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "Write learner-visible directions like concise textbook tasks" in prompt
    assert "use at most two sentences" in prompt
    assert "A1 and A2 task directions in prompt_md, stem_md, and per-item cues use concise English" in prompt
    assert "B1 and higher task directions use natural Bokmål" in prompt
    assert "Keep assessor language, scoring rules, and rubric reasoning out of prompt_md and stem_md" in prompt


def test_exercise_author_prompt_given_route_authority_expect_match_pairs_gated_on_route() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "Choose the operation required by each request's `evidence_route`; do not change the route" in prompt
    assert "operation=match_pairs" in prompt[prompt.index("pair_matching:") :].split("\n", 1)[0]
    assert "Each compact checkpoint request must become exactly one exercise" in prompt
    assert "fail the stage rather than weaken the evidence goal" in prompt


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


def test_preservation_prompt_given_lost_negative_status_expect_advisory_finding() -> None:
    prompt = build_preservation_review_prompt(
        plan_text="plan",
        draft_text="draft",
        lesson_md="lesson",
        exercise_requests_yaml="[]",
    )

    assert "inline compiler-recognized negative marker" in prompt
    assert "surrounding heading" in prompt
    assert "possible loss for author inspection" in prompt
    assert "does not authorize edits or block" in prompt


def test_exercise_author_prompt_given_translated_teaching_context_expect_supplied_answer_retrieval_guard() -> None:
    prompt = build_exercise_author_prompt(
        plan_text="plan", lesson_md="Vi viser film. We show films.", exercise_requests_yaml="[]"
    )

    assert "supplied_answer_retrieval" in prompt
    assert "temporarily ignore the Norwegian text" in prompt
    assert "stems, options, and segments" in prompt
    assert "do not state the very distinction being tested" in prompt


def test_rich_draft_prompt_given_size_guidance_expect_size_and_supplied_answer_guard() -> None:
    prompt = build_rich_draft_prompt("plan", plan_kind="grammar", size_guidance="Aim for 11-14 sections.")

    assert "Aim for 11-14 sections." in prompt
    assert "without a length or section-count target" not in prompt
    assert "never supply an exercise target answer through a translation, gloss, or copied form" in prompt


def test_workbench_author_profile_given_authoring_contract_expect_supplied_answer_guard() -> None:
    profile = (Path(__file__).resolve().parents[3] / ".opencode/agents/workbench-author.md").read_text()

    assert "supplied_answer_retrieval" in profile
    assert "temporarily ignore the Norwegian text" in profile
    assert "Never supply the target form beside a recall gap" in profile
