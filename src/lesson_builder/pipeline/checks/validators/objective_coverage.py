"""Entry point: ``objective_coverage_check``.

Deterministic check: every declared objective must appear in at least one
section's ``objective_ids``. This complements ``objective_structural_check``,
which verifies that objective IDs referenced by exercises/sections resolve and
that no objective is unused by *either* exercises or sections. This check adds
the stricter requirement that every objective is *taught* in a section, not
merely practiced in an exercise.

Findings are ``advisory`` (warning): an objective can legitimately be
exercise-only by design (e.g. ``future_perfect`` / ``obj_diagnose``,
``hvis_vs_om`` / ``obj_optional_om``), so this must not hard-block. Its value is
as a corruption *signal* — the section-objective mis-stamping bug showed up as
~90 lessons with coverage gaps, which this surfaces loudly without failing legit
practice-only objectives.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.defect_rules import section_coverage_gaps
from lesson_builder.pipeline.checks.result import CheckResult


def objective_coverage_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag declared objectives that do not appear in any section's objective_ids."""
    results: list[CheckResult] = []
    for oid in section_coverage_gaps(data):
        results.append(
            CheckResult(
                check_id="objective_coverage",
                severity="warning",
                advisory=True,
                unit_id=oid,
                message=(
                    f"objective '{oid}' is not covered by any section's objective_ids"
                ),
                fix_hint=(
                    f"Add objective_id '{oid}' to at least one section's objective_ids, "
                    f"or drop the objective if it is not taught."
                ),
            )
        )
    return results
