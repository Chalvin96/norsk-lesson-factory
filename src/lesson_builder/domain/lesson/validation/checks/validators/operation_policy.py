"""Entry point: `operation_policy_check` validates exercise policy."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from typing import cast

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.operations import OperationPolicy
from lesson_builder.domain.lesson.models.operations import is_oracle_operation
from lesson_builder.domain.lesson.models.operations import operation_policy

K_OPERATION_POLICY_MIN_DIALOGUE_SPEAKERS = 2
K_OPERATION_POLICY_MIN_DIALOGUE_TURNS = 2


def operation_policy_check(data: dict[str, Any], *, content_kind: str | None = None) -> list[CheckResult]:
    """Enforce operation, Bloom, and grammar-evidence rules."""
    results: list[CheckResult] = []
    exercises_by_objective: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for exercise in _exercises(data):
        operation = str(exercise.get("operation", ""))
        policy = operation_policy(operation)
        if policy is None:
            continue
        results.extend(_operation_bloom_findings(exercise, operation, policy, content_kind))
        objective_id = exercise.get("objective_id")
        if isinstance(objective_id, str) and objective_id:
            exercises_by_objective[objective_id].append(exercise)

    if content_kind == "grammar":
        results.extend(_grammar_evidence_findings(exercises_by_objective))
    if content_kind == "communicative":
        results.extend(_communicative_dialogue_findings(data))
    return results


def _operation_bloom_findings(
    exercise: dict[str, Any], operation: str, policy: OperationPolicy, content_kind: str | None
) -> list[CheckResult]:
    """Return a Bloom eligibility finding for one policy-backed exercise."""
    if content_kind is None or exercise.get("bloom_level") in policy.bloom_levels:
        return []
    bloom_level = exercise.get("bloom_level")
    return [
        CheckResult(
            check_id="operation_bloom",
            severity="blocker" if operation == "speak" else "warning",
            unit_id=str(exercise.get("id", "")),
            message=(
                f"operation '{operation}' is not eligible at Bloom '{bloom_level}'; "
                f"allowed levels: {list(policy.bloom_levels)}"
            ),
            fix_hint=(
                f"Retag the exercise at one of {list(policy.bloom_levels)}, "
                "or choose an operation eligible for the objective's Bloom target."
            ),
        )
    ]


def _grammar_evidence_findings(
    exercises_by_objective: defaultdict[str, list[dict[str, Any]]],
) -> list[CheckResult]:
    """Find grammar objectives without a text-gradable exercise."""
    results: list[CheckResult] = []
    for objective_id, exercises in exercises_by_objective.items():
        if any(is_oracle_operation(str(exercise.get("operation", ""))) for exercise in exercises):
            continue
        results.append(
            CheckResult(
                check_id="objective_evidence",
                severity="blocker",
                unit_id=objective_id,
                message=f"grammar objective '{objective_id}' has no text-gradable exercise",
                fix_hint=(
                    "Add at least one choose, recall_fill, match_pairs, judge, "
                    "categorize, build, or find_fix exercise for this objective."
                ),
            )
        )
    return results


def _communicative_dialogue_findings(data: dict[str, Any]) -> list[CheckResult]:
    """Find communicative lessons without a named-speaker dialogue."""
    dialogue_groups, dialogue_turns = _collect_dialogue_evidence(data)
    if any(
        len(speakers) >= K_OPERATION_POLICY_MIN_DIALOGUE_SPEAKERS
        and dialogue_turns[group] >= K_OPERATION_POLICY_MIN_DIALOGUE_TURNS
        for group, speakers in dialogue_groups.items()
    ):
        return []
    return [
        CheckResult(
            check_id="communicative_dialogue",
            severity="blocker",
            unit_id="communicative-dialogue",
            message="communicative lesson must contain a named-speaker dialogue with at least two turns",
            fix_hint=(
                "Mark each reading turn with dialogue_id, speaker_id, and speaker_name; "
                "use a natural complete exchange without imposing a fixed turn count."
            ),
        )
    ]


def _collect_dialogue_evidence(data: dict[str, Any]) -> tuple[defaultdict[str, set[str]], defaultdict[str, int]]:
    """Collect named speakers and turns by dialogue ID."""
    dialogue_groups: defaultdict[str, set[str]] = defaultdict(set)
    dialogue_turns: defaultdict[str, int] = defaultdict(int)
    for section in _sections(data):
        for block in section.get("blocks", []):
            if not isinstance(block, dict) or block.get("kind") != "reading":
                continue
            dialogue_id = block.get("dialogue_id")
            speaker_id = block.get("speaker_id")
            speaker_name = block.get("speaker_name")
            if not all(isinstance(value, str) and value.strip() for value in (dialogue_id, speaker_id, speaker_name)):
                continue
            dialogue_groups[cast(str, dialogue_id)].add(cast(str, speaker_id))
            dialogue_turns[cast(str, dialogue_id)] += 1
    return dialogue_groups, dialogue_turns


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return exercise elements from an internal lesson mapping."""
    return [
        element
        for element in data.get("elements", [])
        if isinstance(element, dict) and element.get("element_kind") == "exercise"
    ]


def _sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return compiled section elements for category-specific checks."""
    return [
        element
        for element in data.get("elements", [])
        if isinstance(element, dict) and element.get("element_kind") == "section"
    ]


__all__ = ["operation_policy_check"]
