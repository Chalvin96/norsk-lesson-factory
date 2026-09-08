"""Entry point: ``human_gate_node`` (registered as ``human_gate``).

``build_lesson_generation_graph`` parks the run at this node: it interrupts at
the artifact boundary and projects the resumed human decision into lifecycle
state before graph routing.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_DEFER
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_REJECT
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_DEFERRED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_PARKED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_REJECTED
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


def human_gate_node(state: LessonGenerationState, _deps: LessonGenerationDeps) -> dict[str, Any]:
    """Interrupt at the artifact boundary and project the human decision."""
    decision = interrupt(_human_interrupt_payload(state))
    return _apply_decision(decision)


def _human_interrupt_payload(state: LessonGenerationState) -> dict[str, Any]:
    """Build a review payload containing hashes and all derived artifacts."""
    export_doc = state.get("export_doc") or {}
    return {
        "action_request": {
            "action": "review_catalog_generation",
            "args": {"run_id": state.get("run_id", "")},
        },
        "config": {
            "allow_accept": True,
            "allow_defer": True,
            "allow_reject": True,
            "allow_edit": False,
        },
        "description": {
            "stage": state.get("stage"),
            "source_hash": state.get("source_hash"),
            "semantic_hash": export_doc.get("semantic_hash"),
            "dependency_hash": export_doc.get("dependency_hash"),
            "export_hash": export_doc.get("export_hash"),
            "scheduled_slot": state.get("scheduled_slot"),
            "source_files": state.get("source_files", []),
            "generation_job": state.get("generation_job"),
            "generated_source_dir": state.get("generated_source_dir"),
            "llm_receipt_path": state.get("llm_receipt_path"),
            "coverage": _coverage_review_summary(state),
            "prepared_at": datetime.now(UTC).isoformat(),
        },
    }


def _apply_decision(decision: dict[str, Any] | None) -> dict[str, Any]:
    """Project a human decision into lifecycle state before graph routing."""
    payload = decision or {}
    status = payload.get("status")
    if status == K_LESSON_GENERATION_DECISION_DEFER:
        stage = K_LESSON_GENERATION_STAGE_DEFERRED
    elif status == K_LESSON_GENERATION_DECISION_REJECT:
        stage = K_LESSON_GENERATION_STAGE_REJECTED
    else:
        stage = K_LESSON_GENERATION_STAGE_PARKED
    return {"human_decision": payload, "stage": stage}


def _coverage_review_summary(state: LessonGenerationState) -> dict[str, Any] | None:
    """Project scratch coverage status into the human-gate review payload."""
    receipt_path = state.get("llm_receipt_path")
    if not receipt_path:
        return None
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"status": "receipt_unavailable"}
    coverage = receipt.get("coverage")
    return coverage if isinstance(coverage, dict) else {"status": "unconfigured"}


__all__ = ["human_gate_node"]
