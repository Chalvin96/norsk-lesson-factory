"""Entry points: ``load_existing_lessons`` and ``match_existing_lesson``.

The catalog-design graph reads the current Markdown/YAML lesson packages. It
does not import the removed JSON authoring corpus or maintain a second lesson
registry. JSON is derived only at the distribution boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.export_distribution import create_lesson_packet_from_source
from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.catalog.services.normalization import normalize_text
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workspace.paths import WorkspacePaths


def load_existing_lessons(repo_root: Path) -> list[ExistingLesson]:
    """Load the current lesson-owner snapshot from Markdown/YAML packages."""
    root = Path(repo_root)
    paths = WorkspacePaths(root)
    lessons_root = paths.lessons_root
    if not lessons_root.exists():
        return []

    plan_slots = _plan_slots(paths.curriculum_plan)
    lessons: list[ExistingLesson] = []
    for source_dir in sorted(path for path in lessons_root.iterdir() if path.is_dir()):
        lesson = _load_existing_lesson(source_dir, plan_slots)
        if lesson is not None:
            lessons.append(lesson)
    return lessons


def match_existing_lesson(
    candidate: CatalogCandidate,
    existing_lessons: list[ExistingLesson],
) -> ExistingLesson | None:
    """Return an exact slug/title match; leave semantic overlap to catalog review."""
    candidate_keys = {
        normalize_slug(candidate.slug),
        normalize_slug(candidate.title),
        *(normalize_slug(label) for label in candidate.alternate_labels),
    }
    candidate_keys.discard("")
    for lesson in existing_lessons:
        lesson_keys = {
            normalize_slug(lesson.slug),
            normalize_slug(lesson.title),
            *(normalize_slug(alias) for alias in lesson.aliases),
        }
        if candidate_keys & {key for key in lesson_keys if key}:
            return lesson
    return None


def _plan_slots(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    slots = payload.get("slots")
    if not isinstance(slots, list):
        return {}
    return {
        str(slot.get("catalog_id")): slot
        for slot in slots
        if isinstance(slot, dict) and str(slot.get("catalog_id", "")).strip()
    }


def _load_existing_lesson(
    source_dir: Path,
    plan_slots: dict[str, dict[str, Any]],
) -> ExistingLesson | None:
    """Build one lesson snapshot when its authored package is complete."""
    if not all((source_dir / name).is_file() for name in ("plan.md", "lesson.md", "exercises.yaml")):
        return None
    export, metadata = create_lesson_packet_from_source(source_dir)
    slug = source_dir.name
    slot = plan_slots.get(slug, {})
    objective_values = [item for item in (export.get("objectives") or []) if isinstance(item, dict)]
    teaching_point_ids = [
        f"existing:{slug}:{item.get('id')}" for item in objective_values if str(item.get("id", "")).strip()
    ]
    objective_summary = "; ".join(
        str(item.get("statement", "")).strip() for item in objective_values if str(item.get("statement", "")).strip()
    )
    return ExistingLesson(
        slug=slug,
        title=str(export.get("title") or slot.get("title") or slug.replace("_", " ")),
        aliases=[],
        objective_summary=objective_summary,
        notes=str(metadata.get("notes") or slot.get("notes") or "").strip(),
        teaching_point_ids=teaching_point_ids,
        chapter=str(metadata.get("family_id") or slot.get("family_id") or ""),
    )


__all__ = [
    "load_existing_lessons",
    "match_existing_lesson",
    "normalize_slug",
    "normalize_text",
]
