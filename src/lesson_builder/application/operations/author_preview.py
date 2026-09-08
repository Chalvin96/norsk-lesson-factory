"""Entry point: `build_author_preview` assembles a read-only lesson preview."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.load_lesson import load_lesson
from lesson_builder.application.operations.load_lesson import load_plan_metadata
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.workspace.paths import WorkspacePaths


@dataclass(frozen=True)
class AuthorPreview:
    """All provider-free data needed by the local preview renderer."""

    lesson_id: str
    source_label: str
    lesson_source: str
    exercise_source: str
    plan_source: str
    lesson: dict[str, Any] | None
    packet: dict[str, Any] | None
    audit: dict[str, Any]
    errors: tuple[str, ...]


def build_author_preview(repo_root: Path, lesson_ref: str) -> AuthorPreview:
    """Load, audit, and project one authored lesson without providers or writes."""
    root = Path(repo_root).resolve()
    package = _resolve_package(root, lesson_ref)
    lesson_source = _read_text(package / "lesson.md")
    exercise_source = _read_text(package / "exercises.yaml")
    plan_source = _read_text(package / "plan.md")
    errors: list[str] = []
    audit: dict[str, Any]
    try:
        audit_result = audit_source_directory(package)
        audit = audit_result.model_dump(mode="json")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        audit = {
            "status": "invalid",
            "exercise_handles": [],
            "marker_handles": [],
            "built_answers": [],
            "findings": [],
        }
        errors.append(f"Source audit could not complete: {exc}")

    lesson: dict[str, Any] | None = None
    packet: dict[str, Any] | None = None
    try:
        loaded = load_lesson(package)
        lesson = loaded.model_dump(mode="json")
        plan = load_plan_metadata(package / "plan.md")
        character_registry = load_optional_character_registry(package)
        packet = DistributionService().create_lesson_packet(
            loaded,
            kind=plan.get("kind"),
            character_registry=character_registry,
            allow_unregistered_characters=character_registry is None,
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"Lesson compile preview could not complete: {exc}")

    lesson_id = package.name
    if lesson is not None:
        lesson_id = str(lesson.get("key", lesson_id))
    return AuthorPreview(
        lesson_id=lesson_id,
        source_label=package.relative_to(root).as_posix(),
        lesson_source=lesson_source,
        exercise_source=exercise_source,
        plan_source=plan_source,
        lesson=lesson,
        packet=packet,
        audit=audit,
        errors=tuple(errors),
    )


def _resolve_package(root: Path, lesson_ref: str) -> Path:
    """Resolve only a lesson id or repository-relative content lesson path."""
    raw = Path(lesson_ref)
    if raw.is_absolute() or not lesson_ref.strip():
        raise ValueError("lesson must be an id or a path under content/lessons")
    if raw.parts[:2] == ("content", "lessons"):
        candidate = root.joinpath(*raw.parts)
    else:
        candidate = WorkspacePaths(root).lessons_root.joinpath(*raw.parts)
    lessons_root = WorkspacePaths(root).lessons_root.resolve()
    resolved = candidate.resolve()
    if resolved != lessons_root and lessons_root not in resolved.parents:
        raise ValueError("lesson must resolve under content/lessons")
    if not resolved.is_dir():
        raise ValueError(f"lesson package not found: {raw.as_posix()}")
    for filename in ("lesson.md", "exercises.yaml", "plan.md"):
        source_path = (resolved / filename).resolve()
        if lessons_root not in source_path.parents or not source_path.is_file():
            raise ValueError(f"lesson package is missing {filename}")
    return resolved


def _read_text(path: Path) -> str:
    """Read a source file for the source view with a bounded failure value."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"[Unable to read {path.name}: {exc}]"


__all__ = ["AuthorPreview", "build_author_preview"]
