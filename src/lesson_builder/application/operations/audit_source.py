"""Entry points: `audit_source_directory` and `audit_source_text`.

Deterministic exercise policy lives in
:mod:`lesson_builder.domain.lesson.validation.source_audit`; this module only
reads source files, parses the authored document, and preserves
compiler-validation findings before delegating to that policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.load_exercises import load_exercises
from lesson_builder.domain.lesson.models.source_audit import K_SOURCE_AUDIT_EXERCISES_FILE
from lesson_builder.domain.lesson.models.source_audit import K_SOURCE_AUDIT_LESSON_FILE
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.models.source_audit import MechanicalFinding
from lesson_builder.domain.lesson.models.source_audit import ReviewArtifact
from lesson_builder.domain.lesson.validation.source_audit import audit_exercise_source
from lesson_builder.formats.yaml import load_unique_yaml


def audit_source_directory(source_dir: Path) -> MechanicalAudit:
    """Audit one authored source directory without invoking an LLM."""
    root = Path(source_dir)
    findings: list[MechanicalFinding] = []
    lesson_text = _read_source_text(
        root / K_SOURCE_AUDIT_LESSON_FILE,
        K_SOURCE_AUDIT_LESSON_FILE,
        findings,
    )
    exercises_text = _read_source_text(
        root / K_SOURCE_AUDIT_EXERCISES_FILE,
        K_SOURCE_AUDIT_EXERCISES_FILE,
        findings,
    )
    return _audit_source_text(
        exercises_text,
        lesson_text=lesson_text,
        initial_findings=findings,
    )


def audit_source_text(
    exercises_text: str,
    *,
    lesson_text: str | None = None,
) -> MechanicalAudit:
    """Audit authored exercise YAML text without filesystem access.

    Repair callers use this entry point after changing the YAML transport. If
    no lesson text is supplied, a marker-only lesson is derived from the
    parsed exercise handles so YAML structure can still be audited.
    """
    return _audit_source_text(exercises_text, lesson_text=lesson_text)


def _audit_source_text(
    exercises_text: str,
    *,
    lesson_text: str | None,
    initial_findings: Iterable[MechanicalFinding] = (),
) -> MechanicalAudit:
    """Parse and validate source text before delegating to domain policy."""
    parsed_findings: list[MechanicalFinding] = []
    raw_items = _raw_exercises(exercises_text, parsed_findings)
    validation_findings: list[MechanicalFinding] = []
    source_validated = False
    if raw_items is not None:
        try:
            load_exercises(exercises_text)
            source_validated = True
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            validation_findings.append(
                MechanicalFinding(
                    code="exercise-source-invalid",
                    severity="blocking",
                    artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                    location="document",
                    evidence=str(exc),
                    explanation=(
                        "The existing exercise loader could not validate the authored "
                        "YAML, so semantic review must not be allowed to claim a pass."
                    ),
                )
            )
    if lesson_text is None:
        handles = [str(item.get("handle")) for item in raw_items or [] if isinstance(item.get("handle"), str)]
        markers = "\n".join(f"{{{{exercise: {handle}}}}}" for handle in handles)
        lesson_text = f"---\nslug: review\n---\n{markers}\n"
    return audit_exercise_source(
        lesson_text,
        raw_items,
        source_validated=source_validated,
        initial_findings=initial_findings,
        parsed_findings=parsed_findings,
        validation_findings=validation_findings,
    )


def _read_source_text(path: Path, artifact: ReviewArtifact, findings: list[MechanicalFinding]) -> str:
    """Read one authored file and record missing/read failures."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        findings.append(
            MechanicalFinding(
                code="source-file-unreadable",
                severity="blocking",
                artifact=artifact,
                location="file",
                evidence=str(exc),
                explanation="The review source is incomplete or unreadable.",
            )
        )
        return ""


def _raw_exercises(text: str, findings: list[MechanicalFinding]) -> list[dict[str, Any]] | None:
    """Parse the raw exercise list so validation retains source ids."""
    try:
        raw = load_unique_yaml(text)
    except ValueError as exc:
        findings.append(
            MechanicalFinding(
                code="exercise-source-invalid",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                location="document",
                evidence=str(exc),
                explanation="The exercise source violates the strict YAML/source contract.",
            )
        )
        return None
    except (TypeError, yaml.YAMLError) as exc:
        findings.append(
            MechanicalFinding(
                code="exercise-yaml-malformed",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                location="document",
                evidence=str(exc),
                explanation="The exercise source is not parseable YAML.",
            )
        )
        return None
    if not isinstance(raw, list):
        findings.append(
            MechanicalFinding(
                code="exercise-list-invalid",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                location="document",
                evidence=type(raw).__name__,
                explanation="exercises.yaml must contain a list of exercise mappings.",
            )
        )
        return None
    if not raw:
        findings.append(
            MechanicalFinding(
                code="exercise-list-empty",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                location="document",
                evidence="[]",
                explanation="A compilable lesson package must contain at least one exercise.",
            )
        )
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            findings.append(
                MechanicalFinding(
                    code="exercise-item-invalid",
                    severity="blocking",
                    artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                    location=f"item[{index}]",
                    evidence=type(item).__name__,
                    explanation="Each exercise must be a YAML mapping.",
                )
            )
        else:
            items.append(item)
    return items


__all__ = [
    "audit_source_directory",
    "audit_source_text",
]
