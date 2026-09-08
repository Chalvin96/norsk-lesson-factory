"""Entry point: ``run_rich_generation_graph`` runs the real rich graph in tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from lesson_builder.workflow.lesson_generation.dependencies import ArtifactGenerationResult
from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.graph import build_lesson_generation_graph
from lesson_builder.workflow.lesson_generation.nodes import stages as stage_module
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DEFAULT_JOB
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


def run_rich_generation_graph(
    *,
    plan_source: Path,
    output_root: Path,
    repo_root: Path,
    run_id: str,
    job: str = K_LESSON_GENERATION_DEFAULT_JOB,
    closed_task_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    standalone_verifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ArtifactGenerationResult:
    """Run the production rich stages through their checkpointed graph."""
    state: LessonGenerationState = {
        "run_id": run_id,
        "repo_root": str(repo_root),
        "output_root": str(output_root),
        "fixture_source": str(plan_source),
        "generation_job": job,
    }
    with pytest.MonkeyPatch.context() as patch:
        if closed_task_verifier is not None:
            patch.setattr(stage_module, "closed_task_verifier_for", lambda _repo_root: closed_task_verifier)
        if standalone_verifier is not None:
            patch.setattr(stage_module, "standalone_review_verifier_for", lambda _repo_root: standalone_verifier)
        values = build_lesson_generation_graph(LessonGenerationDeps(), checkpointer=MemorySaver()).invoke(
            state, config={"configurable": {"thread_id": run_id}}
        )
    source_dir = Path(values["generated_source_dir"])
    receipt_path = Path(values["llm_receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    return ArtifactGenerationResult(source_dir=source_dir, receipt_path=receipt_path, receipt=receipt)
