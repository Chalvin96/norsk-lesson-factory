"""Entry point: `prepare_source_package` prepares one authored package in memory.

The operation reads the authored Markdown and YAML once, audits and loads that
same snapshot, and returns the reusable projections. Workflow code persists
the derived sidecars and adds checkpoint and scheduling metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lesson_builder.application.operations.compile_lesson_package import compile_lesson_source_snapshot
from lesson_builder.application.operations.compile_lesson_package import load_authored_source_snapshot
from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.services.exercise_diagnostics import analyze_exercise_diagnostics
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.domain.lesson.settings import K_TRANSCRIPT_AUDIO_LANGUAGE

K_SOURCE_PACKAGE_PLAN_FILE = "plan.md"
K_SOURCE_PACKAGE_SCHEMA_VERSION = "0.1-catalog-package"
K_SOURCE_PACKAGE_COURSE_ID = "nb-course-catalog-generation"
K_SOURCE_PACKAGE_HASH_PREFIX = "sha256:"


@dataclass(frozen=True)
class SourcePackagePreparation:
    """Reusable in-memory result of preparing an authored source package."""

    audit: MechanicalAudit
    lesson: Lesson
    plan: dict[str, Any]
    export_doc: dict[str, Any]
    export_hash: str
    exercise_diagnostics: dict[str, Any]
    transcript_document: dict[str, Any]


def prepare_source_package(
    source_dir: Path,
    *,
    terminology_bans: TerminologyBans | None = None,
) -> SourcePackagePreparation:
    """Audit and project one authored package from one exact source snapshot."""
    root = Path(source_dir)
    source = load_authored_source_snapshot(root)
    compiled = compile_lesson_source_snapshot(
        source,
        source_dir=root,
        terminology_bans=terminology_bans,
    )
    audit = compiled.audit
    lesson = compiled.lesson
    if lesson is None:
        raise ValueError("strict lesson-package compilation did not produce a lesson")
    plan = compiled.plan
    internal_lesson = lesson.model_dump(mode="json")
    kind = plan.get("kind")
    lesson_export = DistributionService().create_lesson_packet(
        lesson,
        kind=kind,
        character_registry=load_optional_character_registry(root),
    )
    exercises = [
        element
        for element in internal_lesson["elements"]
        if isinstance(element, dict) and element.get("element_kind") == "exercise"
    ]
    if not exercises:
        raise ValueError("lesson package must contain at least one exercise")
    diagnostics = analyze_exercise_diagnostics(
        lesson,
        expected_objective_ids=_plan_objective_ids(plan),
    ).model_dump(mode="json")
    quality_results = list(compiled.quality_results)
    blocking_results = [result for result in quality_results if result.is_blocking]
    if blocking_results:
        details = "; ".join(f"{result.check_id}({result.unit_id}): {result.message}" for result in blocking_results)
        raise ValueError(f"deterministic gate blocked lesson-package compilation: {details}")
    transcript_blocks = derive_transcript_blocks(internal_lesson)
    transcript_document = {
        "package_version": str(plan.get("package_version", K_SOURCE_PACKAGE_SCHEMA_VERSION)),
        "lesson_id": str(plan.get("lesson_id", "")),
        "language": K_TRANSCRIPT_AUDIO_LANGUAGE,
        "derived": True,
        "blocks": transcript_blocks,
    }
    audio_declarations = _audio_declarations_from_transcript_blocks(transcript_blocks)
    semantic_hash = hash_json({"kind": kind, "lesson": lesson_export, "exercises": exercises})
    dependency_hash = hash_json(
        {
            "transcript_blocks": [
                {key: block[key] for key in ("id", "text", "origin", "audio")} for block in transcript_blocks
            ],
            "audio_declarations": audio_declarations,
        }
    )
    export_doc = {
        "schema_version": K_SOURCE_PACKAGE_SCHEMA_VERSION,
        "course_id": K_SOURCE_PACKAGE_COURSE_ID,
        "artifact_id": f"lesson:{lesson.key}",
        "kind": kind,
        "lesson": lesson_export,
        "exercises": exercises,
        "transcript_ids": [block["id"] for block in transcript_blocks],
        "transcript_blocks": transcript_blocks,
        "audio_declarations": audio_declarations,
        "mechanical_preflight": audit.model_dump(mode="json"),
        "deterministic_checks": [result.model_dump(mode="json") for result in quality_results],
        "semantic_hash": semantic_hash,
        "dependency_hash": dependency_hash,
    }
    return SourcePackagePreparation(
        audit=audit,
        lesson=lesson,
        plan=plan,
        export_doc=export_doc,
        export_hash=hash_json(export_doc),
        exercise_diagnostics=diagnostics,
        transcript_document=transcript_document,
    )


def hash_json(value: object) -> str:
    """Return a stable hash for a JSON-compatible value."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{K_SOURCE_PACKAGE_HASH_PREFIX}{hashlib.sha256(payload).hexdigest()}"


def _plan_objective_ids(plan: dict[str, Any]) -> set[str]:
    """Return non-empty objective identifiers declared by plan metadata."""
    objectives = plan.get("objectives", [])
    if not isinstance(objectives, list):
        return set()
    return {
        item["id"] for item in objectives if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }


def _audio_declarations_from_transcript_blocks(
    transcript_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project the audio metadata carried by each derived transcript block."""
    result: list[dict[str, Any]] = []
    for block in transcript_blocks:
        transcript_id = str(block["id"])
        audio = block.get("audio")
        if not isinstance(audio, dict):
            raise TypeError(f"transcript block {transcript_id!r} requires audio metadata")
        origin = str(block.get("origin", "authored_source"))
        purpose = {
            "lesson_example": "lesson example",
            "lesson_reading": "lesson reading",
            "exercise_target": "exercise target",
        }.get(origin, "authored Norwegian source")
        result.append(
            {
                "audio_id": f"audio-{transcript_id}",
                "transcript_id": transcript_id,
                "purpose": purpose,
                "role": str(audio.get("role", "model")),
                "language": str(audio.get("language", K_TRANSCRIPT_AUDIO_LANGUAGE)),
            }
        )
    return result


__all__ = [
    # Preparation contract and operation
    "SourcePackagePreparation",
    "prepare_source_package",
    # Shared deterministic hash
    "hash_json",
]
