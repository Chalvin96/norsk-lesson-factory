"""Entry point: ``verify_attestations_node`` checks generated stage evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


def verify_attestations_node(state: LessonGenerationState) -> dict[str, Any]:
    """Refuse generated packages whose content-addressed evidence is invalid."""
    source_dir = state.get("generated_source_dir")
    if not source_dir:
        raise ValueError("generated lesson package has no source directory")
    issues = verify_source_attestations(Path(source_dir))
    if issues:
        raise ValueError("generated source attestations are invalid: " + "; ".join(issues))
    return {}


__all__ = ["verify_attestations_node"]
