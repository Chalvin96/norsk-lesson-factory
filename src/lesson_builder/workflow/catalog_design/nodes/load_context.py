"""Entry point: ``load_context_node`` (registered as ``load_context``).

``build_catalog_graph`` calls this node at graph entry to validate the request
and snapshot the existing lessons once for every later node.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.workflow.catalog_design.context import load_existing_lessons
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def load_context_node(state: CatalogState, _deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Validate the request and snapshot existing lessons once at graph entry."""
    request = CatalogRequest.model_validate(state.get("request", {}))
    existing_payload = state.get("existing_lessons")
    if existing_payload is None:
        repo_root = Path(str(state.get("repo_root") or Path.cwd()))
        existing_lessons = load_existing_lessons(repo_root)
    else:
        existing_lessons = [ExistingLesson.model_validate(item) for item in existing_payload]
    return {
        "request": request.model_dump(mode="json"),
        "existing_lessons": [lesson.model_dump(mode="json") for lesson in existing_lessons],
        "iteration": int(state.get("iteration", 0)),
        "max_iterations": request.max_iterations,
        "stagnation_count": int(state.get("stagnation_count", 0)),
        "advice_used": bool(state.get("advice_used", False)),
        "advice": state.get("advice"),
        "architecture_question": False,
        "architecture_question_reason": "",
        "coverage_complete": False,
        "coverage_gaps": [],
        "errors": [],
    }


__all__ = ["load_context_node"]
