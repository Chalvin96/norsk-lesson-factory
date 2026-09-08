"""Entry points: `render_plan`, `load_plan`, `select_catalog_ids`, `build_coverage_id`, `hash_text`, and `build_slot_plan_hash`.

This module owns the pure batch-plan projection: rendering catalog-approved
slot metadata, validating plan selection, and deriving the exact plan hash used
for cache compatibility. The batch workflow calls these helpers while preparing
and scheduling approved slots; this module does not generate lessons or write
batch state.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from typing import cast

import yaml

from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_RUN_ID_MAX_LENGTH


def render_plan(slot: CurriculumPlanSlot) -> str:
    """Render the authoritative plan brief supplied to the author."""
    boundary_id = build_coverage_id(slot.catalog_id, suffix="-boundary")
    claims = [point.statement for point in slot.teaching_points] or [slot.learner_outcome]
    objectives = [
        {
            "id": build_coverage_id(slot.catalog_id, prefix="obj", index=index),
            "statement": claim,
        }
        for index, claim in enumerate(claims, start=1)
    ]
    required_coverage = [
        {
            "id": build_coverage_id(slot.catalog_id, index=index),
            "objective": objective["id"],
            "claim": claim,
            "scope": "required",
            "evidence": ["explanation", "example", "controlled_practice"],
        }
        for index, (objective, claim) in enumerate(zip(objectives, claims, strict=True), start=1)
    ]
    metadata: dict[str, Any] = {
        "package_version": "0.1-catalog-batch",
        "lesson_id": slot.catalog_id,
        "kind": slot.catalog_kind,
        "title": slot.title,
        "cefr_level": slot.cefr_level,
        "cefr_target_basis": "earliest_approved_tag",
        "approved": True,
        "catalog_id": slot.catalog_id,
        "learner_outcome": slot.learner_outcome,
        "family_id": slot.family_id,
        "cefr_tags": list(slot.cefr_tags),
        "teaching_points": [point.model_dump(mode="json") for point in slot.teaching_points],
        "terminology_ids": list(slot.terminology_ids),
        "prerequisites": list(slot.prerequisites),
        "helpful_prerequisites": list(slot.helpful_prerequisites),
        "objectives": objectives,
        "coverage": [
            *required_coverage,
            {
                "id": boundary_id,
                "claim": (
                    f"Keep this {slot.catalog_kind} lesson focused on the approved outcome; "
                    "do not add a second learner outcome."
                ),
                "scope": "guardrail",
                "evidence": ["whole_lesson"],
            },
        ],
        "success_criteria": claims,
        "exclusions": ["open speaking assessment", "learner-specific scoring"],
    }
    return (
        "---\n"
        + cast(str, yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
        + "---\n\n"
        + f"# Plan — {slot.title}\n\n"
        + "## Pedagogical job\n\n"
        + f"Teach this approved catalog outcome: {slot.learner_outcome}\n\n"
        + "An independent instructional brief precedes the author stage. Do not "
        "replace it with free-form prose or add a second learner outcome.\n"
    )


def load_plan(path: Path) -> CurriculumPlan:
    """Load and validate one planner output."""
    if not path.exists():
        raise FileNotFoundError(f"catalog plan not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("catalog plan must be a YAML mapping")
    return CurriculumPlan.model_validate(payload)


def select_catalog_ids(plan: CurriculumPlan, catalog_ids: Sequence[str]) -> CurriculumPlan:
    """Restrict a plan to explicit catalog IDs and reject typos."""
    requested = {value.strip() for value in catalog_ids if value.strip()}
    known = {slot.catalog_id for slot in plan.slots}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"catalog IDs are not present in plan: {', '.join(unknown)}")
    if not requested:
        raise ValueError("catalog_ids must contain at least one non-empty ID")
    return plan.model_copy(update={"slots": [slot for slot in plan.slots if slot.catalog_id in requested]})


def build_coverage_id(
    catalog_id: str,
    *,
    suffix: str = "",
    prefix: str = "cov",
    index: int | None = None,
) -> str:
    """Create a coverage ID accepted by the plan contract from a catalog ID."""
    normalized = re.sub(r"[^a-z0-9-]+", "-", catalog_id.lower()).strip("-")
    index_suffix = f"-{index:02d}" if index is not None else ""
    value = f"{prefix}-{normalized}{index_suffix}{suffix}"
    if len(value) <= K_LESSON_GENERATION_RUN_ID_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{value[: K_LESSON_GENERATION_RUN_ID_MAX_LENGTH - len(digest) - 1]}-{digest}"


def hash_text(value: str) -> str:
    """Return a content hash for a rendered catalog slot plan."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_slot_plan_hash(slot: CurriculumPlanSlot) -> str:
    """Hash the exact plan bytes that will be supplied to the author."""
    return hash_text(render_plan(slot))


__all__ = [
    "build_coverage_id",
    "build_slot_plan_hash",
    "hash_text",
    "load_plan",
    "render_plan",
    "select_catalog_ids",
]
