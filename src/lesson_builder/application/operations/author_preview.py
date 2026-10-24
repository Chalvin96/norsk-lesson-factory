"""Entry point: `build_package_preview` supplies a checked lesson view to the workbench."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.compile_lesson_package import collect_lesson_package
from lesson_builder.application.operations.export_distribution import require_plan_kind
from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.domain.distribution.services.distribution_service import DistributionService


@dataclass(frozen=True)
class AuthorPreview:
    """Provider-free source and packet data shown in the workbench."""

    lesson_id: str
    source_label: str
    lesson_source: str
    exercise_source: str
    plan_source: str
    lesson: dict[str, Any] | None
    packet: dict[str, Any] | None
    audit: dict[str, Any]
    errors: tuple[str, ...]


def build_package_preview(package: Path, *, source_label: str) -> AuthorPreview:
    """Audit and project one workbench lesson package without providers or writes."""
    lesson_source = _read_text(package / "lesson.md")
    exercise_source = _read_text(package / "exercises.yaml")
    plan_source = _read_text(package / "plan.md")
    errors: list[str] = []
    compiled = collect_lesson_package(package)
    audit = compiled.audit.model_dump(mode="json")
    errors.extend(f"compile preview: {error}" for error in compiled.errors)
    errors.extend(
        "compile preview: deterministic gate blocked "
        f"{result.check_id} ({result.unit_id}): {result.message}"
        + (f" Fix: {result.fix_hint}" if result.fix_hint else "")
        for result in compiled.quality_results
        if result.is_blocking
    )
    lesson: dict[str, Any] | None = None
    packet: dict[str, Any] | None = None
    if compiled.lesson is not None:
        lesson = compiled.lesson.model_dump(mode="json")
        plan = compiled.plan
        if (
            not compiled.errors
            and not compiled.audit.material_findings
            and not any(result.is_blocking for result in compiled.quality_results)
        ):
            try:
                character_registry = load_optional_character_registry(package)
                packet = DistributionService().create_lesson_packet(
                    compiled.lesson,
                    kind=require_plan_kind(plan, plan_label="plan.md"),
                    character_registry=character_registry,
                    allow_unregistered_characters=character_registry is None,
                )
            except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
                errors.append(f"Lesson packet preview could not complete: {exc}")

    lesson_id = package.name
    if lesson is not None:
        lesson_id = str(lesson.get("key", lesson_id))
    return AuthorPreview(
        lesson_id=lesson_id,
        source_label=source_label,
        lesson_source=lesson_source,
        exercise_source=exercise_source,
        plan_source=plan_source,
        lesson=lesson,
        packet=packet,
        audit=audit,
        errors=tuple(errors),
    )


def _read_text(path: Path) -> str:
    """Read a source file for the source view with a bounded failure value."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"[Unable to read {path.name}: {exc}]"


__all__ = ["AuthorPreview", "build_package_preview"]
