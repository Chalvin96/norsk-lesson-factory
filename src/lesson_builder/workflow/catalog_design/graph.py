"""Entry point: ``build_catalog_graph``.

The graph is the catalog pipeline: load -> parallel explorer/review discovery ->
normalize -> exact existing filter -> catalog-review resolution -> quality evaluation ->
bounded iteration/advice/finalization.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies
from lesson_builder.workflow.catalog_design.dependencies import default_catalog_design_dependencies
from lesson_builder.workflow.catalog_design.nodes.discover_explorer import discover_explorer_node
from lesson_builder.workflow.catalog_design.nodes.discover_reviewer import discover_reviewer_node
from lesson_builder.workflow.catalog_design.nodes.evaluate_candidates import evaluate_candidates_node
from lesson_builder.workflow.catalog_design.nodes.existing_filter import existing_filter_node
from lesson_builder.workflow.catalog_design.nodes.finalize import finalize_catalog_node
from lesson_builder.workflow.catalog_design.nodes.iterate import iterate_node
from lesson_builder.workflow.catalog_design.nodes.load_context import load_context_node
from lesson_builder.workflow.catalog_design.nodes.normalize_candidates import normalize_candidates_node
from lesson_builder.workflow.catalog_design.nodes.request_advice import request_advice_node
from lesson_builder.workflow.catalog_design.nodes.resolve_candidates import resolve_candidates_node
from lesson_builder.workflow.catalog_design.state import K_ROUTE_ADVICE
from lesson_builder.workflow.catalog_design.state import K_ROUTE_FINALIZE
from lesson_builder.workflow.catalog_design.state import K_ROUTE_ITERATE
from lesson_builder.workflow.catalog_design.state import CatalogState
from lesson_builder.workflow.catalog_design.state import route_after_evaluation


def build_catalog_graph(
    deps: CatalogDesignDependencies | None = None,
    *,
    checkpointer: bool | BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the checkpointable catalog-design LangGraph."""
    graph_deps = deps or default_catalog_design_dependencies()
    graph = StateGraph(CatalogState)
    graph.add_node("load_context", lambda state: load_context_node(state, graph_deps))
    graph.add_node("discover_explorer", lambda state: discover_explorer_node(state, graph_deps))
    graph.add_node("discover_reviewer", lambda state: discover_reviewer_node(state, graph_deps))
    graph.add_node("normalize_candidates", lambda state: normalize_candidates_node(state, graph_deps))
    graph.add_node("existing_filter", lambda state: existing_filter_node(state, graph_deps))
    graph.add_node("resolve_candidates", lambda state: resolve_candidates_node(state, graph_deps))
    graph.add_node("evaluate_candidates", lambda state: evaluate_candidates_node(state, graph_deps))
    graph.add_node("request_advice", lambda state: request_advice_node(state, graph_deps))
    graph.add_node("iterate", lambda state: iterate_node(state, graph_deps))
    graph.add_node("finalize", lambda state: finalize_catalog_node(state, graph_deps))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "discover_explorer")
    graph.add_edge("load_context", "discover_reviewer")
    graph.add_edge("discover_explorer", "normalize_candidates")
    graph.add_edge("discover_reviewer", "normalize_candidates")
    graph.add_edge("normalize_candidates", "existing_filter")
    graph.add_edge("existing_filter", "resolve_candidates")
    graph.add_edge("resolve_candidates", "evaluate_candidates")
    graph.add_conditional_edges(
        "evaluate_candidates",
        route_after_evaluation,
        {
            K_ROUTE_ITERATE: "iterate",
            K_ROUTE_ADVICE: "request_advice",
            K_ROUTE_FINALIZE: "finalize",
        },
    )
    graph.add_edge("iterate", "discover_explorer")
    graph.add_edge("iterate", "discover_reviewer")
    graph.add_edge("request_advice", "resolve_candidates")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


__all__ = ["build_catalog_graph"]
