"""Entry point: ``gate_lesson_results``.

``gate_lesson_results`` runs the deterministic validators and returns the raw
``list[CheckResult]`` the graph's checks node consumes (per-issue granularity for
routing/audit). The graph spine reads ``is_blocking`` off these results directly —
there is no separate summary/manifest vocabulary.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.checks.validators.answer_leak import answer_leak_check
from lesson_builder.domain.lesson.validation.checks.validators.bloom_alignment import bloom_alignment_check
from lesson_builder.domain.lesson.validation.checks.validators.canned_opener import canned_opener_check
from lesson_builder.domain.lesson.validation.checks.validators.find_fix_integrity import find_fix_integrity_check
from lesson_builder.domain.lesson.validation.checks.validators.language_slot import language_slot_check
from lesson_builder.domain.lesson.validation.checks.validators.nynorsk import nynorsk_check
from lesson_builder.domain.lesson.validation.checks.validators.objective_coverage import objective_coverage_check
from lesson_builder.domain.lesson.validation.checks.validators.objective_structural import objective_structural_check
from lesson_builder.domain.lesson.validation.checks.validators.operation_policy import operation_policy_check
from lesson_builder.domain.lesson.validation.checks.validators.regression import regression_check
from lesson_builder.domain.lesson.validation.checks.validators.render_bug import render_bug_check
from lesson_builder.domain.lesson.validation.checks.validators.schema_validate import schema_validate
from lesson_builder.domain.lesson.validation.checks.validators.structural import exercise_structural_checks
from lesson_builder.domain.lesson.validation.checks.validators.terminology import terminology_check


def gate_lesson_results(
    lesson: dict[str, Any],
    *,
    terminology_bans: TerminologyBans,
    baseline_export: dict[str, Any] | None = None,
    content_kind: str | None = None,
) -> list[CheckResult]:
    """Run the gate validators and return the raw per-issue CheckResult list.

    The package runtime reads this for routing (blocking) and audit. The caller
    supplies terminology policy because this domain gate does not read files.
    Schema failure short-circuits (returns only the schema results).
    """
    slug = _resolve_slug(lesson)
    schema_results = schema_validate(lesson)
    if schema_results:
        return list(schema_results)
    results: list[CheckResult] = []
    results.extend(exercise_structural_checks(lesson))
    results.extend(operation_policy_check(lesson, content_kind=content_kind))
    results.extend(objective_structural_check(lesson))
    results.extend(objective_coverage_check(lesson))
    results.extend(bloom_alignment_check(lesson))
    results.extend(nynorsk_check(lesson))
    results.extend(regression_check(slug, lesson, baseline_export))
    results.extend(render_bug_check(lesson))
    results.extend(find_fix_integrity_check(lesson))
    results.extend(terminology_check(lesson, terminology_bans))
    results.extend(answer_leak_check(lesson))
    results.extend(canned_opener_check(lesson))
    results.extend(language_slot_check(lesson))
    return results


def _resolve_slug(lesson: dict[str, Any]) -> str:
    return str(lesson.get("concept_slug") or lesson.get("key") or "unknown")
