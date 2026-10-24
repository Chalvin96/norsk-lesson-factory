"""Entry points: `validate_approved_source`, `validate_approved_plan`, and `approved_content_hash`."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_ALLOWED_KINDS
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_HASH_ALGO
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_PLAN_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_REQUIRED_FILES
from lesson_builder.application.operations.load_lesson import load_plan_metadata
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter


def validate_approved_source(fixture_source: Path) -> dict[str, Any]:
    """Validate authoring shape, approval, identity, and protected source hash."""
    fixture_source = Path(fixture_source)
    missing = [name for name in K_LESSON_GENERATION_REQUIRED_FILES if not (fixture_source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"lesson-package fixture missing required file(s): {', '.join(missing)}")
    plan = load_plan_metadata(fixture_source / K_LESSON_GENERATION_PLAN_FILE)
    if plan.get("approved") is not True:
        raise ValueError("lesson-package fixture plan.md must contain approved: true")
    kind = plan.get("kind")
    if kind not in K_LESSON_GENERATION_ALLOWED_KINDS:
        raise ValueError(f"plan.md kind must be one of {list(K_LESSON_GENERATION_ALLOWED_KINDS)!r}")
    lesson_id = plan.get("lesson_id")
    if not isinstance(lesson_id, str) or not lesson_id:
        raise ValueError("plan.md must contain a non-empty lesson_id")
    expected_hash = approved_content_hash(fixture_source)
    if plan.get("approved_source_hash") != expected_hash:
        raise ValueError(
            "plan.md approved_source_hash does not match lesson.md, exercises.yaml, "
            f"and plan.md content (expected {expected_hash})"
        )
    lesson_frontmatter, _ = parse_source_frontmatter(
        (fixture_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8")
    )
    if lesson_frontmatter.get("slug") != lesson_id:
        raise ValueError("plan.md lesson_id must match lesson.md slug")
    return plan


def validate_approved_plan(fixture_source: Path) -> dict[str, Any]:
    """Validate the approved plan boundary used before model generation."""
    fixture_source = Path(fixture_source)
    missing = [name for name in K_LESSON_GENERATION_REQUIRED_FILES if not (fixture_source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"lesson-package plan missing required file(s): {', '.join(missing)}")
    plan = load_plan_metadata(fixture_source / K_LESSON_GENERATION_PLAN_FILE)
    if plan.get("approved") is not True:
        raise ValueError("lesson-package plan.md must contain approved: true")
    kind = plan.get("kind")
    if kind not in K_LESSON_GENERATION_ALLOWED_KINDS:
        raise ValueError(f"plan.md kind must be one of {list(K_LESSON_GENERATION_ALLOWED_KINDS)!r}")
    lesson_id = plan.get("lesson_id")
    if not isinstance(lesson_id, str) or not lesson_id:
        raise ValueError("plan.md must contain a non-empty lesson_id")
    return plan


def approved_content_hash(source_dir: Path) -> str:
    """Return the protected package hash with its self-hash field normalized."""
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    plan_metadata, plan_body = parse_source_frontmatter(
        (Path(source_dir) / K_LESSON_GENERATION_PLAN_FILE).read_text(encoding="utf-8")
    )
    plan_body = plan_body.lstrip("\n")
    plan_metadata.pop("approved_source_hash", None)
    normalized_plan = yaml.safe_dump(plan_metadata, allow_unicode=True, sort_keys=True) + plan_body
    hasher.update(K_LESSON_GENERATION_PLAN_FILE.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(normalized_plan.encode("utf-8"))
    for name in (K_LESSON_GENERATION_LESSON_FILE, K_LESSON_GENERATION_EXERCISES_FILE):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((Path(source_dir) / name).read_bytes())
    return hasher.hexdigest()


__all__ = [
    "approved_content_hash",
    "validate_approved_plan",
    "validate_approved_source",
]
