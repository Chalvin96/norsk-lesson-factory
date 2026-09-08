"""Entry point: `analyze_exercise_diagnostics` records factory evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import cast

from lesson_builder.domain.lesson.models.elements import CategorizePayload
from lesson_builder.domain.lesson.models.elements import Exercise
from lesson_builder.domain.lesson.models.elements import MatchPairsPayload
from lesson_builder.domain.lesson.models.elements import RecallFillPayload
from lesson_builder.domain.lesson.models.exercise_diagnostics import ExerciseDiagnostics
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.models.operations import operation_policy

K_EXERCISE_DIAGNOSTICS_MIN_EXERCISES = 4
K_EXERCISE_DIAGNOSTICS_CONCENTRATION_THRESHOLD = 0.6
K_EXERCISE_DIAGNOSTICS_MIN_OPTIONS = 2


def analyze_exercise_diagnostics(
    lesson: Lesson, *, expected_objective_ids: Iterable[str] | None = None
) -> ExerciseDiagnostics:
    """Record authored response evidence without judging learner behavior.

    The analyzer receives one validated internal lesson shape. Phase labels are
    inferred from the operation as a transparent stage proxy. Contract gaps are
    returned in ``findings``; heuristic observations are returned in
    ``diagnostics``. It never changes an exercise and does not treat a raw
    exercise count as a quality score.
    """
    exercises = [element for element in lesson.elements if element.element_kind == "exercise"]
    (
        operation_counts,
        objective_counts,
        bloom_counts,
        phase_counts,
        response_opportunities,
        binary_blank_slots,
        blank_slots,
    ) = _count_exercise_evidence(exercises)
    findings = _diagnostic_findings(
        lesson,
        exercises,
        objective_counts,
        expected_objective_ids=expected_objective_ids,
    )
    dominant_operation_share = _dominant_share(operation_counts, len(exercises))
    binary_option_saturation = _ratio(binary_blank_slots, blank_slots)
    diagnostics = _diagnostic_observations(
        exercises,
        operation_counts,
        bloom_counts,
        phase_counts,
        dominant_operation_share=dominant_operation_share,
        binary_option_saturation=binary_option_saturation,
        blank_slots=blank_slots,
    )

    return ExerciseDiagnostics(
        status="needs_human" if findings else "observed",
        total_exercises=len(exercises),
        response_opportunities=response_opportunities,
        operation_counts=dict(sorted(operation_counts.items())),
        objective_counts=dict(sorted(objective_counts.items())),
        bloom_counts=dict(sorted(bloom_counts.items())),
        phase_counts=dict(sorted(phase_counts.items())),
        dominant_operation_share=dominant_operation_share,
        binary_option_saturation=binary_option_saturation,
        findings=findings,
        diagnostics=diagnostics,
    )


def _count_exercise_evidence(
    exercises: list[Exercise],
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str], int, int, int]:
    """Collect deterministic counts from validated exercise values."""
    operation_counts: Counter[str] = Counter()
    objective_counts: Counter[str] = Counter()
    bloom_counts: Counter[str] = Counter()
    phase_counts: Counter[str] = Counter()
    response_opportunities = 0
    binary_blank_slots = 0
    blank_slots = 0
    for exercise in exercises:
        operation_counts[exercise.operation] += 1
        objective_counts[exercise.objective_id.strip() or "unassigned"] += 1
        bloom_counts[exercise.bloom_level] += 1
        policy = operation_policy(exercise.operation)
        phase_counts[policy.build_stage if policy is not None else "unclassified"] += 1
        response_opportunities += _response_opportunities(exercise)
        if exercise.operation == "recall_fill":
            binary_slots, total_slots = _recall_option_shape(exercise)
            binary_blank_slots += binary_slots
            blank_slots += total_slots
    return (
        operation_counts,
        objective_counts,
        bloom_counts,
        phase_counts,
        response_opportunities,
        binary_blank_slots,
        blank_slots,
    )


def _diagnostic_findings(
    lesson: Lesson,
    exercises: list[Exercise],
    objective_counts: Counter[str],
    *,
    expected_objective_ids: Iterable[str] | None,
) -> list[str]:
    """Return concrete contract gaps that require human remediation."""
    findings = ["no_exercises"] if not exercises else []
    expected = (
        {objective.id for objective in lesson.objectives if objective.id}
        if expected_objective_ids is None
        else set(expected_objective_ids)
    )
    uncovered = sorted(expected - set(objective_counts))
    findings.extend(f"objective_without_exercise:{objective}" for objective in uncovered)
    return findings


def _diagnostic_observations(
    exercises: list[Exercise],
    operation_counts: Counter[str],
    bloom_counts: Counter[str],
    phase_counts: Counter[str],
    *,
    dominant_operation_share: float,
    binary_option_saturation: float,
    blank_slots: int,
) -> list[str]:
    """Return heuristic observations that do not block compilation."""
    diagnostics = [
        f"unverified_open:{exercise.operation}:{exercise.id}"
        for exercise in exercises
        if exercise.operation in {"speak", "write"}
    ]
    if "apply" in bloom_counts and not set(operation_counts) & {"build", "write", "speak"}:
        diagnostics.append("apply_without_application_operation")
    if len(exercises) >= K_EXERCISE_DIAGNOSTICS_MIN_EXERCISES and len(operation_counts) == 1:
        diagnostics.append("single_operation_concentration")
    if phase_counts.get("notice", 0) and phase_counts.get("transfer", 0) and not phase_counts.get("controlled", 0):
        diagnostics.append("phase_gap:controlled")
    if (
        len(exercises) >= K_EXERCISE_DIAGNOSTICS_MIN_EXERCISES
        and dominant_operation_share >= K_EXERCISE_DIAGNOSTICS_CONCENTRATION_THRESHOLD
    ):
        diagnostics.append("dominant_operation_concentration")
    if (
        blank_slots >= K_EXERCISE_DIAGNOSTICS_MIN_EXERCISES
        and binary_option_saturation >= K_EXERCISE_DIAGNOSTICS_CONCENTRATION_THRESHOLD
    ):
        diagnostics.append("binary_option_saturation")
    return diagnostics


def _response_opportunities(exercise: Exercise) -> int:
    """Count answerable learner responses from the typed payload."""
    payload = exercise.payload
    if exercise.operation == "recall_fill":
        payload = cast(RecallFillPayload, payload)
        return max(1, sum(segment.kind == "blank" for segment in payload.segments))
    if exercise.operation == "match_pairs":
        payload = cast(MatchPairsPayload, payload)
        return max(1, len(payload.pairs))
    if exercise.operation == "categorize":
        payload = cast(CategorizePayload, payload)
        return max(1, len(payload.items))
    return 1


def _recall_option_shape(exercise: Exercise) -> tuple[int, int]:
    """Count recall blanks and those reduced to binary option choices."""
    if exercise.operation != "recall_fill":
        return 0, 0
    blanks = [segment for segment in exercise.payload.segments if segment.kind == "blank"]
    binary = sum(len(segment.options) == K_EXERCISE_DIAGNOSTICS_MIN_OPTIONS for segment in blanks)
    return binary, len(blanks)


def _dominant_share(counts: Counter[str], total: int) -> float:
    """Return the share of the most frequent operation, or zero when empty."""
    return max(counts.values(), default=0) / total if total else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    """Return a bounded ratio while keeping empty evidence neutral."""
    return numerator / denominator if denominator else 0.0
