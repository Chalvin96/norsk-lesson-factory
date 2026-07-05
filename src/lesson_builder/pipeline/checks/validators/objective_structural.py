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

from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.concept_requirements import K_PLACEHOLDER_OBJECTIVE_RE

# One canonical placeholder pattern, defined at the card write-boundary
# (concept_requirements) and reused here so the card guard and the lesson gate
# cannot drift. Aliased for back-compat with existing references/tests.
K_PLACEHOLDER_RE = K_PLACEHOLDER_OBJECTIVE_RE


def objective_structural_check(data: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    objective_ids = {obj["id"] for obj in data.get("objectives", []) if isinstance(obj, dict) and obj.get("id")}

    # 0. Placeholder statement: a raw "Learn <slug>" (anchored, slug-shape only).
    #    Real objective sentences never match because they contain spaces/punctuation
    #    beyond the single slug token.
    for obj in data.get("objectives", []):
        if not isinstance(obj, dict):
            continue
        statement = obj.get("statement", "")
        if isinstance(statement, str) and K_PLACEHOLDER_RE.match(statement):
            results.append(
                CheckResult(
                    check_id="objective_placeholder",
                    severity="blocker",
                    unit_id=str(obj.get("id", "")),
                    message='objective statement is a raw placeholder ("Learn <slug>")',
                    fix_hint="Replace with a real objective sentence.",
                )
            )

    # 1. Exercise -> objective linkage.
    referenced: set[str] = set()
    for ex in _exercises(data):
        ex_id = str(ex.get("id", ""))
        obj_id = ex.get("objective_id")
        if not obj_id:
            continue
        referenced.add(obj_id)
        if obj_id not in objective_ids:
            results.append(
                CheckResult(
                    check_id="objective_structural",
                    severity="blocker",
                    unit_id=ex_id,
                    message=f"exercise references unknown objective_id '{obj_id}'",
                    fix_hint=f"Re-tag the exercise with a declared objective id in {sorted(objective_ids)}.",
                )
            )

    # 2. Section -> objective linkage (model/contrast carry exactly 1; orient/recap may carry 0+).
    for sec in _sections(data):
        sec_id = str(sec.get("id", ""))
        for obj_id in sec.get("objective_ids", []) or []:
            referenced.add(obj_id)
            if obj_id not in objective_ids:
                results.append(
                    CheckResult(
                        check_id="objective_structural",
                        severity="blocker",
                        unit_id=sec_id,
                        message=f"section references unknown objective_id '{obj_id}'",
                        fix_hint=f"Re-tag the section with declared objective ids in {sorted(objective_ids)}.",
                    )
                )

    # 3. Review pool objective/key linkage.
    review_pool = data.get("review_pool") or {}
    for pool in review_pool.get("pools", []) or []:
        pool_obj = pool.get("objective_id")
        if pool_obj and pool_obj not in objective_ids:
            results.append(
                CheckResult(
                    check_id="objective_structural",
                    severity="blocker",
                    message=f"review_pool references unknown objective_id '{pool_obj}'",
                    fix_hint=f"Re-key the pool with a declared objective id in {sorted(objective_ids)}.",
                )
            )

    # 4. Declared-but-unused objective. Every objective must be taught (section) or
    #    practiced (exercise). The review pool is metadata around exercises, not an
    #    independent use of the objective — a pool-only objective is still unused.
    for oid in objective_ids - referenced:
        results.append(
            CheckResult(
                check_id="objective_structural",
                severity="blocker",
                unit_id=oid,
                message=f"objective '{oid}' is declared but unused by any exercise or section",
                fix_hint=f"Add at least one exercise/section tagged with objective_id '{oid}', or drop the objective.",
            )
        )

    return results


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "exercise"
    ]


def _sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "section"
    ]
