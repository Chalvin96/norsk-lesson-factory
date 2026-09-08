"""Tests for the shared exercise-operation policy registry."""

from __future__ import annotations

import pytest
import yaml

from lesson_builder.application.operations.load_exercises import load_exercises
from lesson_builder.domain.lesson.models.operations import evidence_route_bloom_levels
from lesson_builder.domain.lesson.models.operations import evidence_route_operations
from lesson_builder.domain.lesson.models.operations import is_oracle_operation
from lesson_builder.domain.lesson.models.operations import operation_names
from lesson_builder.domain.lesson.models.operations import operation_policy
from lesson_builder.domain.lesson.models.operations import render_evidence_route_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_payload_guidance


def test_is_oracle_operation_given_text_and_spoken_operations_expect_classification():
    assert is_oracle_operation("choose")
    assert not is_oracle_operation("speak")


def test_render_operation_guidance_given_registry_expect_all_operations_once():
    guidance = render_operation_guidance()

    assert "match_pairs = remember" in guidance
    assert "write = apply" in guidance
    assert "payload=" in guidance
    assert "stage=" not in guidance
    assert "build_stage" not in guidance


def test_render_operation_guidance_given_registry_expect_source_field_names():
    guidance = render_operation_guidance()

    assert "payload=audio_target, segments" in guidance
    assert "payload=left, right, pairs" in guidance
    assert "payload=sentence_md, is_correct, feedback" in guidance
    assert "payload=buckets, items" in guidance
    assert "payload=tokens, error_token_id, feedback" in guidance
    assert "payload=response_language, min_words, max_words, judge_prompt, criteria" in guidance
    assert "left_right_pairs" not in guidance
    assert "buckets_and_items" not in guidance
    assert "tokens_with_error" not in guidance
    assert "open_response" not in guidance


def test_render_operation_payload_guidance_given_registry_expect_all_concrete_shapes():
    guidance = render_operation_payload_guidance()

    for field in (
        "segments:",
        "left:",
        "right:",
        "pairs:",
        "sentence_md:",
        "is_correct:",
        "buckets:",
        "items:",
        "tokens:",
        "answer_order:",
        "error_token_id:",
        "target:",
        "response_language:",
        "judge_prompt:",
        "criteria:",
    ):
        assert field in guidance

    assert "Use the exact analogous shapes" not in guidance
    assert "left_right_pairs" not in guidance


def test_render_operation_payload_guidance_given_recall_and_choose_examples_expect_consistent_safe_examples():
    guidance = render_operation_payload_guidance()

    assert 'audio_target: "I dag kommer jeg hjem."' in guidance
    assert "  - id: option-a" in guidance
    assert "  - id: option-b" in guidance
    assert "  - id: correct" not in guidance
    assert "  - id: incorrect" not in guidance


def test_evidence_route_operations_given_sentence_construction_expect_build_only():
    assert evidence_route_operations("sentence_construction") == ("build",)


def test_evidence_route_operations_given_unknown_route_expect_empty_tuple():
    assert evidence_route_operations("not-a-route") == ()


def test_evidence_route_bloom_levels_given_pair_matching_expect_remember_only():
    assert evidence_route_bloom_levels("pair_matching") == ("remember",)


def test_render_evidence_route_guidance_given_registry_expect_routes_and_operations():
    guidance = render_evidence_route_guidance()

    assert "sentence_construction" in guidance
    assert "operation=build" in guidance
    assert "allowed_bloom=apply" in guidance
    assert "open_production" in guidance


@pytest.mark.parametrize("operation", sorted(operation_names()))
def test_payload_examples_given_registry_example_expect_real_loader_round_trip(
    operation: str,
):
    policy = operation_policy(operation)
    assert policy is not None
    example = render_operation_payload_guidance([operation]).split("```yaml\n", 1)[1].split("\n```", 1)[0]
    document = _wrap_payload_example(operation, example)

    loaded = load_exercises(document)

    assert len(loaded) == 1
    exercise = loaded[0]
    assert exercise.operation == operation
    assert exercise.objective_id == "obj-registry"
    assert exercise.bloom_level == policy.default_bloom
    if operation == "write":
        # YAML 1.1 parses a bare `no` as a boolean; the canonical example must
        # quote the ISO-639-1 code so the loader receives the string.
        assert yaml.safe_load(example)["response_language"] == "no"
        assert exercise.payload.response_language == "no"


def _wrap_payload_example(operation: str, example: str) -> str:
    """Wrap one registry payload example in the public exercise wrapper."""
    policy = operation_policy(operation)
    assert policy is not None
    indented = "\n".join(f"  {line}" if line.strip() else "  " for line in example.strip("\n").splitlines())
    return (
        "- handle: registry-example\n"
        f"  op: {operation}\n"
        "  objective: obj-registry\n"
        f"  bloom: {policy.default_bloom}\n"
        '  prompt_md: "Use the registry payload example."\n'
        f"{indented}\n"
    )
