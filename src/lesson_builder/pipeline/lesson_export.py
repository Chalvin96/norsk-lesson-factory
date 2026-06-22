from __future__ import annotations

from typing import Any

from lesson_builder.schema import ExportedLesson, Lesson, to_export_dict
from lesson_builder.schema.elements import BloomLevel

K_LESSON_EXPORT_IMPORTED_STATEMENT = "[imported_unverified] objective statement not recoverable from export"
K_LESSON_EXPORT_DEFAULT_BLOOM: list[BloomLevel] = ["understand"]  # placeholder; export-dropped field
K_LESSON_EXPORT_DEFAULT_EXERCISE_BLOOM: BloomLevel = "understand"  # placeholder; export-dropped field


def _exercise_objective_map(review_pool: dict[str, Any]) -> dict[str, str]:
    # first pool in document order wins (deterministic tie-break for the 3 multi-pool-exercise lessons)
    mapping: dict[str, str] = {}
    for pool in review_pool["pools"]:
        for card in pool["cards"]:
            mapping.setdefault(card["exercise_id"], pool["objective_id"])
    return mapping


def _element_from_export(
    element: dict[str, Any], index: int, exercise_to_objective: dict[str, str], default_objective: str
) -> dict[str, Any]:
    body = {k: v for k, v in element.items() if k != "kind"}
    if element["kind"] == "section":
        role = body["role"]
        objective_ids = [default_objective] if role in ("model", "contrast") else []
        return {
            "element_kind": "section",
            "id": f"imported_section_{index:03d}",
            "objective_ids": objective_ids,
            **body,
        }
    if element["kind"] == "exercise":
        return {
            "element_kind": "exercise",
            "objective_id": exercise_to_objective.get(body["id"], default_objective),
            "bloom_level": K_LESSON_EXPORT_DEFAULT_EXERCISE_BLOOM,
            "derived_from": [],
            **body,
        }
    raise ValueError(f"unknown exported element kind: {element['kind']!r}")


def lesson_from_export(export: dict[str, Any]) -> Lesson:
    """Build an internal Lesson from an exported lesson dict.

    Recoverable: top-level fields, elements content, review_pool, objective ids + exercise->objective
    (from review_pool). Placeholder (export-dropped): objective.statement/bloom_targets, section
    id/objective_ids, exercise bloom_level. When review_pool has no pools (e.g. the 5 goldens),
    objective ids are NOT recoverable and a single `o1` placeholder is synthesized — still round-trip
    safe because objectives/objective_id/objective_ids are all export-dropped. See
    docs/export-inverse-completeness.md.
    """
    ExportedLesson.model_validate(export)  # firewall + schema_version=="3.0" check
    review_pool = export["review_pool"]
    exercise_to_objective = _exercise_objective_map(review_pool)
    objective_ids = list(dict.fromkeys(p["objective_id"] for p in review_pool["pools"]))
    objective_ids = objective_ids or ["o1"]
    objectives = [
        {
            "id": objective_id,
            "statement": K_LESSON_EXPORT_IMPORTED_STATEMENT,
            "bloom_targets": K_LESSON_EXPORT_DEFAULT_BLOOM,
        }
        for objective_id in objective_ids
    ]
    elements = [
        _element_from_export(element, index, exercise_to_objective, objective_ids[0])
        for index, element in enumerate(export["elements"])
    ]
    internal = {k: export[k] for k in ("key", "concept_slug", "grounding_mode", "title", "cefr_level", "goal")}
    internal |= {"objectives": objectives, "elements": elements, "review_pool": review_pool}
    return Lesson.model_validate(internal)


def lesson_to_export(lesson: Lesson) -> dict[str, Any]:
    """Project internal Lesson -> export dict, validated against the locked ExportedLesson 3.0 contract."""
    export = to_export_dict(lesson)
    ExportedLesson.model_validate(export)  # firewall: fail loudly if projection breaks the contract
    return export
