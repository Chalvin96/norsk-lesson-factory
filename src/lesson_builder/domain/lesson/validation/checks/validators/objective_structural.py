"""Entry point: ``objective_structural_check``.

Deterministic objective-ID connectivity check.

Pydantic guarantees per-element shape (e.g. ``model``/``contrast`` sections carry
exactly one ``objective_id``), but it does not verify that the IDs referenced by
exercises, sections, and the review pool actually resolve to declared
``Lesson.objectives``. That connectivity is this check's job.

Detected defects (all ``blocker``):

- An objective whose ``statement`` is a raw placeholder of the form ``"Learn <slug>"``
  (anchored slug shape; real sentences such as ``"Learn common compounds as whole
  words"`` do NOT match).
- An exercise whose ``objective_id`` is not in ``objectives[]``.
- A section whose ``objective_ids`` references an unknown objective.
- A review-pool ``Pool.objective_id`` (and therefore ``key``) not in ``objectives[]``.
- An objective declared in ``objectives[]`` but unused by every exercise and section
  (the lesson claims a goal it never teaches or practices).
"""

from __future__ import annotations

import re
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult

# A raw scaffold objective must never reach a lesson package.
K_PLACEHOLDER_RE = re.compile(r"^Learn [a-z0-9_]+$")


def objective_structural_check(data: dict[str, Any]) -> list[CheckResult]:
    objective_ids = {obj["id"] for obj in data.get("objectives", []) if isinstance(obj, dict) and obj.get("id")}
    results = _placeholder_findings(data)
    exercise_findings, exercise_references = _exercise_linkage_findings(data, objective_ids)
    section_findings, section_references = _section_linkage_findings(data, objective_ids)
    results.extend(exercise_findings)
    results.extend(section_findings)
    results.extend(_review_pool_linkage_findings(data, objective_ids))
    results.extend(_unused_objective_findings(objective_ids, exercise_references | section_references))
    return results


def _placeholder_findings(data: dict[str, Any]) -> list[CheckResult]:
    """Find raw scaffold objective statements."""
    return [
        CheckResult(
            check_id="objective_placeholder",
            severity="blocker",
            unit_id=str(obj.get("id", "")),
            message='objective statement is a raw placeholder ("Learn <slug>")',
            fix_hint="Replace with a real objective sentence.",
        )
        for obj in data.get("objectives", [])
        if isinstance(obj, dict)
        and isinstance(obj.get("statement", ""), str)
        and K_PLACEHOLDER_RE.match(obj["statement"])
    ]


def _exercise_linkage_findings(data: dict[str, Any], objective_ids: set[str]) -> tuple[list[CheckResult], set[str]]:
    """Find unknown exercise objectives and return referenced IDs."""
    results: list[CheckResult] = []
    referenced: set[str] = set()
    for exercise in _exercises(data):
        objective_id = exercise.get("objective_id")
        if not objective_id:
            continue
        referenced.add(objective_id)
        if objective_id not in objective_ids:
            results.append(
                CheckResult(
                    check_id="objective_structural",
                    severity="blocker",
                    unit_id=str(exercise.get("id", "")),
                    message=f"exercise references unknown objective_id '{objective_id}'",
                    fix_hint=f"Re-tag the exercise with a declared objective id in {sorted(objective_ids)}.",
                )
            )
    return results, referenced


def _section_linkage_findings(data: dict[str, Any], objective_ids: set[str]) -> tuple[list[CheckResult], set[str]]:
    """Find unknown section objectives and return referenced IDs."""
    results: list[CheckResult] = []
    referenced: set[str] = set()
    for section in _sections(data):
        section_id = str(section.get("id", ""))
        for objective_id in section.get("objective_ids", []) or []:
            referenced.add(objective_id)
            if objective_id not in objective_ids:
                results.append(
                    CheckResult(
                        check_id="objective_structural",
                        severity="blocker",
                        unit_id=section_id,
                        message=f"section references unknown objective_id '{objective_id}'",
                        fix_hint=f"Re-tag the section with declared objective ids in {sorted(objective_ids)}.",
                    )
                )
    return results, referenced


def _review_pool_linkage_findings(data: dict[str, Any], objective_ids: set[str]) -> list[CheckResult]:
    """Find review pools keyed by undeclared objectives."""
    review_pool = data.get("review_pool") or {}
    return [
        CheckResult(
            check_id="objective_structural",
            severity="blocker",
            message=f"review_pool references unknown objective_id '{pool.get('objective_id')}'",
            fix_hint=f"Re-key the pool with a declared objective id in {sorted(objective_ids)}.",
        )
        for pool in review_pool.get("pools", []) or []
        if pool.get("objective_id") and pool.get("objective_id") not in objective_ids
    ]


def _unused_objective_findings(objective_ids: set[str], referenced: set[str]) -> list[CheckResult]:
    """Find objectives that are neither taught nor practiced."""
    return [
        CheckResult(
            check_id="objective_structural",
            severity="blocker",
            unit_id=objective_id,
            message=f"objective '{objective_id}' is declared but unused by any exercise or section",
            fix_hint=f"Add at least one exercise/section tagged with objective_id '{objective_id}', or drop the objective.",
        )
        for objective_id in objective_ids - referenced
    ]


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict) and el.get("element_kind") == "exercise"]


def _sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict) and el.get("element_kind") == "section"]
