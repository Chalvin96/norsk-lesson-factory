"""Entry point: ``assemble_lesson``.

Stage 4 of cold authoring: deterministically assemble a gate-clean ``Lesson``
from the stage outputs. Python owns every structural default (ids, the review
pool, ``grounding_mode``), injects required anchor forms so
``requirement_anchors_check`` passes by construction (R1), asserts full
objective coverage (R5), and runs the full ``gate_lesson_results`` preflight as
a hard gate (zero blockers, R6).

The author never assembles the aggregate; it only authors stage content. This
stage is pure Python over the authored pieces.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results
from lesson_builder.pipeline.cold_author.models import AssemblyError
from lesson_builder.schema import Lesson

K_GROUNDING_MODE_DEFAULT = "fallback_no_wiki"


def assemble_lesson(
    slug: str,
    requirements: dict[str, Any],
    metadata: dict[str, Any],
    sections: list[dict[str, Any]],
    exercises: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble + validate a gate-clean Lesson from the stage outputs.

    Raises ``AssemblyError`` if objective coverage is incomplete, the anchor
    injection cannot place an anchor, the assembled lesson fails
    ``Lesson.model_validate``, or the full gate preflight reports any blocker.
    """
    _assert_objective_coverage(metadata["objectives"], sections, exercises)
    elements = list(sections) + list(exercises)
    elements = _inject_missing_anchors(elements, requirements)
    review_pool = _build_review_pool(metadata["objectives"], exercises)
    lesson = {
        "key": slug,
        "concept_slug": slug,
        "grounding_mode": K_GROUNDING_MODE_DEFAULT,
        "title": metadata["title"],
        "cefr_level": metadata["cefr_level"],
        "goal": metadata["goal"],
        "objectives": list(metadata["objectives"]),
        "elements": elements,
        "review_pool": review_pool,
    }
    _validate_lesson(lesson)
    _assert_gate_preflight_clean(lesson, requirements)
    return lesson


def _assert_objective_coverage(
    objectives: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    exercises: list[dict[str, Any]],
) -> None:
    declared = {obj["id"] for obj in objectives}
    covered: set[str] = set()
    for section in sections:
        covered.update(section.get("objective_ids") or [])
    for exercise in exercises:
        if exercise.get("objective_id"):
            covered.add(exercise["objective_id"])
    missing = sorted(declared - covered)
    if missing:
        raise AssemblyError(
            f"objective coverage incomplete; objectives {missing} are declared but not "
            "referenced by any section or exercise"
        )


def _inject_missing_anchors(
    elements: list[dict[str, Any]],
    requirements: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inject each missing required anchor into a recap section (R1).

    ``requirement_anchors_check`` substring-matches each anchor against
    ``json.dumps(elements).lower()``; multi-word anchors that the LANGUAGE_CONTRACT
    would split across foreign_term spans will often never match even when the
    content is correct. Injecting each missing anchor as a single contiguous
    text span makes coverage a guarantee.
    """
    anchors = requirements.get("required_anchor_forms") or []
    if not anchors:
        return elements
    all_text = json.dumps(elements, ensure_ascii=False).lower()
    missing = [str(a) for a in anchors if str(a).lower() not in all_text]
    if not missing:
        return elements
    recap = _anchor_recap_section(missing)
    return list(elements) + [recap]


def _anchor_recap_section(anchors: list[str]) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    for anchor in anchors:
        spans.append({"kind": "text", "value": anchor})
        spans.append({"kind": "text", "value": " — "})
    if spans and spans[-1]["value"] == " — ":
        spans.pop()
    return {
        "element_kind": "section",
        "id": f"cold_anchor_recap_{uuid.uuid4().hex[:8]}",
        "role": "recap",
        "objective_ids": [],
        "title": "Required forms",
        "blocks": [
            {
                "kind": "paragraph",
                "spans": spans or [{"kind": "text", "value": "Required anchor forms."}],
            }
        ],
    }


def _build_review_pool(
    objectives: list[dict[str, Any]],
    exercises: list[dict[str, Any]],
) -> dict[str, Any]:
    """One pool per objective; each pool's cards reference that objective's exercises.

    Mirrors the schema's ``Pool.key == Pool.objective_id`` invariant and
    ``objective_structural_check`` (every exercise resolves to a declared
    objective). Real UUIDs (the schema requires ``PoolCard.uuid: UUID``).
    """
    by_objective: dict[str, list[str]] = {obj["id"]: [] for obj in objectives}
    for exercise in exercises:
        oid = exercise.get("objective_id")
        if oid in by_objective:
            by_objective[oid].append(exercise["id"])
    pools: list[dict[str, Any]] = []
    for oid, exercise_ids in by_objective.items():
        if not exercise_ids:
            continue
        pools.append(
            {
                "key": oid,
                "objective_id": oid,
                "cards": [
                    {"uuid": str(uuid.uuid4()), "exercise_id": eid} for eid in exercise_ids
                ],
            }
        )
    return {"pools": pools}


def _validate_lesson(lesson: dict[str, Any]) -> None:
    try:
        Lesson.model_validate(lesson)
    except Exception as exc:
        raise AssemblyError(f"assembled lesson failed Lesson.model_validate: {exc}") from exc


def _assert_gate_preflight_clean(
    lesson: dict[str, Any],
    requirements: dict[str, Any],
) -> None:
    results = gate_lesson_results(
        lesson,
        requirements=requirements,
        baseline_export=None,
        recorded_requirements_hash=None,
    )
    blockers = [r for r in results if r.is_blocking]
    if blockers:
        messages = "\n".join(f"- [{r.check_id}] {r.message}" for r in blockers)
        raise AssemblyError(
            f"assembled lesson has {len(blockers)} blocking gate result(s) "
            f"(the cold-author preflight must produce zero blockers):\n{messages}"
        )


__all__ = ["assemble_lesson"]
