"""Validation library. Entry point: individual ``*_check`` functions.

Concrete checkers live here as leaf validators; the gate manager that composes
them stays in ``lesson_builder.pipeline.checks.gate_manager``.
"""

# Import order below is alphabetical by module (ruff/isort-enforced); it does not
# reflect role. See the grouped comments in __all__ for how these exports relate.
from lesson_builder.pipeline.checks.validators.answer_leak import answer_leak_check
from lesson_builder.pipeline.checks.validators.answer_valid import (
    answer_valid_check,
    compare_answers,
)
from lesson_builder.pipeline.checks.validators.bloom_alignment import bloom_alignment_check
from lesson_builder.pipeline.checks.validators.find_fix_integrity import find_fix_integrity_check
from lesson_builder.pipeline.checks.validators.naturalness import (
    NaturalnessReview,
    naturalness_check_from_review,
    naturalness_percent,
)
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

__all__ = [
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
    "naturalness_check_from_review",
    "naturalness_percent",
    "NaturalnessReview",
    # Standalone utilities
    "compare_answers",
]
