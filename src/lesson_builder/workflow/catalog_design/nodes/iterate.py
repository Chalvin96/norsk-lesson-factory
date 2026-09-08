"""Entry point: ``iterate_node`` (registered as ``iterate``).

``build_catalog_graph`` routes to this node for one bounded additional
discovery round; it advances the round counter and clears the architecture
question while keeping advice and candidate lineage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from lesson_builder.workflow.catalog_design.state import CatalogState

if TYPE_CHECKING:
    from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies


def iterate_node(state: CatalogState, _deps: CatalogDesignDependencies) -> dict[str, Any]:
    """Advance the bounded discovery round while keeping advice and lineage."""
    return {
        "iteration": state.get("iteration", 0) + 1,
        "architecture_question": False,
        "architecture_question_reason": "",
    }


__all__ = ["iterate_node"]
