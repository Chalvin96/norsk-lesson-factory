"""Entry point: ``canned_opener_check``.

Deterministic check: flags exercises whose ``explanation[0]`` text span opens
with a canned acknowledgment word (Correct, Good, Right, Yes, ...). The defect
is pervasive in the corpus (~56%) and rooted in the author prompt, so this
ships ADVISORY (``severity="warning"``, ``advisory=True``): it surfaces the
pattern for repair without blocking the bulk of lessons at the gate.

Detection reuses ``is_canned_opener`` from ``defect_rules`` (one shared
implementation shared with the eval harness) and mirrors the ``explanation[0]``
walk used by ``scripts/eval_lesson_defects._canned_opener_stats``.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.defect_rules import is_canned_opener
from lesson_builder.pipeline.checks.result import CheckResult

K_CANNED_OPENER = "canned_opener"


def canned_opener_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag exercises whose first explanation span opens with a canned acknowledgment word."""
    results: list[CheckResult] = []
    for el in data.get("elements", []):
        if not isinstance(el, dict) or el.get("element_kind") != "exercise":
            continue
        explanation = el.get("explanation")
        if not isinstance(explanation, list) or not explanation:
            continue
        first = explanation[0]
        if not (
            isinstance(first, dict)
            and isinstance(first.get("value"), str)
            and first["value"]
        ):
            continue
        if is_canned_opener(first["value"]):
            results.append(
                CheckResult(
                    check_id=K_CANNED_OPENER,
                    severity="warning",
                    advisory=True,
                    unit_id=str(el.get("id", "")),
                    message="explanation opens with a canned acknowledgment word",
                    fix_hint="Start with the reason, not an acknowledgment word.",
                )
            )
    return results
