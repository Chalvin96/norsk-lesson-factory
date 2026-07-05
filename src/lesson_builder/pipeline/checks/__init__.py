"""Check library. Entry point: ``gate_lesson_results``.

This package contains the gate manager and shared check result contract; concrete
``(lesson, ctx) -> [CheckResult]`` validation logic lives in ``validators``.

Deterministic checks drive routing and export gates. Advisory (LLM) checks log
verdicts until explicit threshold config promotes them.
"""

# Import order below is alphabetical by module (ruff/isort-enforced); it does not
# reflect role. See the module docstring for the entry point, and the grouped
# comments in __all__ below for how these exports relate to each other.
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult, Severity
from lesson_builder.pipeline.checks.validators.answer_leak import answer_leak_check
from lesson_builder.pipeline.checks.validators.answer_valid import (
    answer_valid_check,
    compare_answers,
)
from lesson_builder.pipeline.checks.validators.bloom_alignment import bloom_alignment_check
from lesson_builder.pipeline.checks.validators.find_fix_integrity import find_fix_integrity_check
from lesson_builder.pipeline.checks.validators.nynorsk import nynorsk_check, nynorsk_scan
from lesson_builder.pipeline.checks.validators.objective_alignment import (
    ObjectiveAlignmentReview,
    objective_alignment_check_from_review,
)
from lesson_builder.pipeline.checks.validators.objective_coverage import objective_coverage_check
from lesson_builder.pipeline.checks.validators.objective_structural import objective_structural_check
from lesson_builder.pipeline.checks.validators.pedagogy import (
    PedagogyReview,
    pedagogy_check_from_review,
    pedagogy_percent,
)
from lesson_builder.pipeline.checks.validators.regression import regression_check
from lesson_builder.pipeline.checks.validators.render_bug import render_bug_check
from lesson_builder.pipeline.checks.validators.requirement_anchors import requirement_anchors_check
from lesson_builder.pipeline.checks.validators.schema_validate import schema_validate
from lesson_builder.pipeline.checks.validators.stale import requirements_hash, stale_check
from lesson_builder.pipeline.checks.validators.structural import exercise_structural_checks
from lesson_builder.pipeline.checks.validators.terminology import (
    terminology_audit,
    terminology_check,
)


def __getattr__(name: str) -> Any:
    if name == "gate_lesson_results":
        from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results

        return gate_lesson_results
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Gate manager
    "gate_lesson_results",
    # Result type
    "CheckResult",
    "Severity",
    # Deterministic checks
    "schema_validate",
    "exercise_structural_checks",
    "objective_structural_check",
    "objective_coverage_check",
    "bloom_alignment_check",
    "nynorsk_check",
    "nynorsk_scan",
    "requirement_anchors_check",
    "regression_check",
    "stale_check",
    "requirements_hash",
    "render_bug_check",
    "find_fix_integrity_check",
    "terminology_check",
    "terminology_audit",
    "answer_leak_check",
    # Advisory checks
    "answer_valid_check",
    "objective_alignment_check_from_review",
    "ObjectiveAlignmentReview",
    "pedagogy_check_from_review",
    "pedagogy_percent",
    "PedagogyReview",
    # Standalone utilities
    "compare_answers",
]
