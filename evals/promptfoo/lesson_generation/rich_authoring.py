"""Entry point: `resolve_plan_terminology_context` for Promptfoo authoring evaluations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.load_terminology import load_terminology_registry
from lesson_builder.domain.lesson.validation.terminology_registry import render_prompt_context
from lesson_builder.domain.lesson.validation.terminology_registry import resolve_concept_ids
from lesson_builder.formats.markdown.frontmatter import extract_frontmatter
from lesson_builder.workspace.paths import WorkspacePaths


def resolve_plan_terminology_context(plan_text: str, *, repo_root: Path) -> str:
    """Load and render terminology selected by approved plan metadata."""
    metadata = _plan_metadata(plan_text)
    ids = metadata.get("terminology_ids", [])
    ids = [value for value in ids if isinstance(value, str) and value.strip()] if isinstance(ids, list) else []
    if not ids:
        return ""
    registry = load_terminology_registry(WorkspacePaths(repo_root).terminology_registry)
    resolve_concept_ids(registry, ids)
    return render_prompt_context(registry, concept_ids=ids)


def _plan_metadata(plan_text: str) -> dict[str, Any]:
    """Parse one approved plan at the workflow format boundary."""
    metadata = yaml.safe_load(extract_frontmatter(plan_text))
    return metadata if isinstance(metadata, dict) else {}


__all__ = [
    # Evaluation context
    "resolve_plan_terminology_context",
]
