"""Entry point: ``prepare_node`` (registered as ``prepare``).

``build_lesson_generation_graph`` calls this node first. Preparation performs
the source-copy, content compile, and transcript/audio-declaration derivation
boundaries before parking at the human gate. No audio is synthesized before
the human gate; only the explicit distribution exporter reaches a provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.dependencies import derive_scheduled_slot
from lesson_builder.workflow.lesson_generation.dependencies import hash_json
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_COMPLETE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_PARKED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_TRANSCRIPT_FILE
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


def prepare_node(state: LessonGenerationState, deps: LessonGenerationDeps) -> dict[str, Any]:
    """Prepare an approved package and park its derived artifacts for review."""
    fixture_source = Path(state["fixture_source"])
    output_root = Path(state["output_root"])
    run_id = state["run_id"]

    copier = (
        deps.proposal_copier if state.get("generation_stage") == K_LESSON_GENERATION_STAGE_COMPLETE else deps.copier
    )
    source_dir, source_files, source_hash = copier(
        fixture_source=fixture_source,
        output_root=output_root,
        run_id=run_id,
    )
    export_doc, _compiler_hash = deps.compiler(source_dir=source_dir, run_id=run_id)
    exercise_diagnostics = _read_exercise_diagnostics(source_dir)
    transcript_path = source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE
    if transcript_path.is_file() and transcript_path.name not in source_files:
        source_files = [*source_files, transcript_path.name]
    export_doc = dict(export_doc)
    export_doc["source_hash"] = source_hash
    scheduled_slot = derive_scheduled_slot(
        artifact_id=str(export_doc.get("artifact_id") or f"lesson:{run_id}"),
        source_hash=source_hash,
    )
    export_doc["scheduled_slot"] = scheduled_slot
    del _compiler_hash
    export_doc["export_hash"] = hash_json(export_doc)
    return {
        "source_dir": str(source_dir),
        "source_files": source_files,
        "source_hash": source_hash,
        "scheduled_slot": scheduled_slot,
        "export_doc": export_doc,
        "exercise_diagnostics": exercise_diagnostics,
        "curriculum_slot_sha256": state.get("curriculum_slot_sha256"),
        "catalog_id": state.get("catalog_id"),
        "stage": K_LESSON_GENERATION_STAGE_PARKED,
    }


def _read_exercise_diagnostics(source_dir: Path) -> dict[str, Any] | None:
    """Read factory diagnostics from the disposable compile sidecar."""
    path = source_dir / K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


__all__ = ["prepare_node"]
