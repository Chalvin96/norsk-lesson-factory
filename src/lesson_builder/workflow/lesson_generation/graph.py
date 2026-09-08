"""Entry point: ``build_lesson_generation_graph``.

Topology for the lesson-package graph. Re-exports the public node
and deps surface so ``from lesson_builder.workflow.lesson_generation.graph import ...``
resolves every name a caller needs.

    START -> [author -> review -> normalize -> preservation -> intent_review
              -> exercise_author -> exercise_compile -> exercise_verify
              -> coverage -> verify_attestations ->] prepare -> human_gate (interrupt)
             -> [route_after_human]
                ├─ accept -> finalize -> END
                ├─ defer  -> END
                └─ reject -> END

Generation nodes receive one injected stage collaborator and checkpoint only
JSON-compatible state. Filesystem, model, and verifier IO stays behind that
collaborator and the lifecycle dependencies. The human gate uses
``interrupt``; resume supplies a ``Command(resume=...)`` carrying the decision
dict.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.nodes import finalize_node
from lesson_builder.workflow.lesson_generation.nodes import human_gate_node
from lesson_builder.workflow.lesson_generation.nodes import prepare_node
from lesson_builder.workflow.lesson_generation.nodes import verify_attestations_node
from lesson_builder.workflow.lesson_generation.nodes.stages import RichAuthoringStageRunner
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_AUTHOR
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_COVERAGE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_EXERCISE_AUTHOR
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_EXERCISE_COMPILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_EXERCISE_VERIFY
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_FINALIZE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_HUMAN_GATE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_INTENT_REVIEW
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_NORMALIZE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_PREPARE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_PRESERVATION
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_REVIEW
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NODE_VERIFY_ATTESTATIONS
from lesson_builder.workflow.lesson_generation.state import K_ROUTE_END
from lesson_builder.workflow.lesson_generation.state import K_ROUTE_FINALIZE
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState
from lesson_builder.workflow.lesson_generation.state import route_after_human


def route_after_start(state: LessonGenerationState) -> str:
    """Route generation runs through rich stages before source preparation."""
    return (
        K_LESSON_GENERATION_NODE_AUTHOR
        if state.get("generation_job") and not state.get("generated_source_dir")
        else K_LESSON_GENERATION_NODE_PREPARE
    )


def build_lesson_generation_graph(
    deps: LessonGenerationDeps | None = None,
    *,
    checkpointer: bool | BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the lesson-package graph.

    ``deps`` defaults to a file-backed ``LessonGenerationDeps`` (disposable
    scratch writes). ``checkpointer`` enables park/resume at the human gate.
    """
    graph_deps = deps or LessonGenerationDeps()
    rich_stages: RichAuthoringStageRunner = graph_deps.rich_stages
    graph = StateGraph(LessonGenerationState)

    graph.add_node(K_LESSON_GENERATION_NODE_AUTHOR, rich_stages.author)
    graph.add_node(K_LESSON_GENERATION_NODE_REVIEW, rich_stages.review)
    graph.add_node(K_LESSON_GENERATION_NODE_NORMALIZE, rich_stages.normalize)
    graph.add_node(K_LESSON_GENERATION_NODE_PRESERVATION, rich_stages.preservation)
    graph.add_node(K_LESSON_GENERATION_NODE_INTENT_REVIEW, rich_stages.intent_review)
    graph.add_node(K_LESSON_GENERATION_NODE_EXERCISE_AUTHOR, rich_stages.exercise_author)
    graph.add_node(K_LESSON_GENERATION_NODE_EXERCISE_COMPILE, rich_stages.exercise_compile)
    graph.add_node(K_LESSON_GENERATION_NODE_EXERCISE_VERIFY, rich_stages.exercise_verify)
    graph.add_node(K_LESSON_GENERATION_NODE_COVERAGE, rich_stages.coverage)
    graph.add_node(K_LESSON_GENERATION_NODE_VERIFY_ATTESTATIONS, verify_attestations_node)
    graph.add_node(K_LESSON_GENERATION_NODE_PREPARE, lambda state: prepare_node(state, graph_deps))
    graph.add_node(K_LESSON_GENERATION_NODE_HUMAN_GATE, lambda state: human_gate_node(state, graph_deps))
    graph.add_node(K_LESSON_GENERATION_NODE_FINALIZE, lambda state: finalize_node(state, graph_deps))

    graph.add_conditional_edges(
        START,
        route_after_start,
        {
            K_LESSON_GENERATION_NODE_AUTHOR: K_LESSON_GENERATION_NODE_AUTHOR,
            K_LESSON_GENERATION_NODE_PREPARE: K_LESSON_GENERATION_NODE_PREPARE,
        },
    )
    graph.add_edge(K_LESSON_GENERATION_NODE_AUTHOR, K_LESSON_GENERATION_NODE_REVIEW)
    graph.add_edge(K_LESSON_GENERATION_NODE_REVIEW, K_LESSON_GENERATION_NODE_NORMALIZE)
    graph.add_edge(K_LESSON_GENERATION_NODE_NORMALIZE, K_LESSON_GENERATION_NODE_PRESERVATION)
    graph.add_edge(K_LESSON_GENERATION_NODE_PRESERVATION, K_LESSON_GENERATION_NODE_INTENT_REVIEW)
    graph.add_edge(K_LESSON_GENERATION_NODE_INTENT_REVIEW, K_LESSON_GENERATION_NODE_EXERCISE_AUTHOR)
    graph.add_edge(K_LESSON_GENERATION_NODE_EXERCISE_AUTHOR, K_LESSON_GENERATION_NODE_EXERCISE_COMPILE)
    graph.add_edge(K_LESSON_GENERATION_NODE_EXERCISE_COMPILE, K_LESSON_GENERATION_NODE_EXERCISE_VERIFY)
    graph.add_edge(K_LESSON_GENERATION_NODE_EXERCISE_VERIFY, K_LESSON_GENERATION_NODE_COVERAGE)
    graph.add_edge(K_LESSON_GENERATION_NODE_COVERAGE, K_LESSON_GENERATION_NODE_VERIFY_ATTESTATIONS)
    graph.add_edge(K_LESSON_GENERATION_NODE_VERIFY_ATTESTATIONS, K_LESSON_GENERATION_NODE_PREPARE)
    graph.add_edge(K_LESSON_GENERATION_NODE_PREPARE, K_LESSON_GENERATION_NODE_HUMAN_GATE)
    graph.add_conditional_edges(
        K_LESSON_GENERATION_NODE_HUMAN_GATE,
        route_after_human,
        {
            K_ROUTE_FINALIZE: K_LESSON_GENERATION_NODE_FINALIZE,
            K_ROUTE_END: END,
            K_LESSON_GENERATION_NODE_HUMAN_GATE: K_LESSON_GENERATION_NODE_HUMAN_GATE,
        },
    )
    graph.add_edge(K_LESSON_GENERATION_NODE_FINALIZE, END)

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "LessonGenerationDeps",
    "build_lesson_generation_graph",
    "route_after_start",
    "verify_attestations_node",
    "prepare_node",
    "human_gate_node",
    "finalize_node",
    "K_LESSON_GENERATION_NODE_PREPARE",
    "K_LESSON_GENERATION_NODE_AUTHOR",
    "K_LESSON_GENERATION_NODE_REVIEW",
    "K_LESSON_GENERATION_NODE_NORMALIZE",
    "K_LESSON_GENERATION_NODE_PRESERVATION",
    "K_LESSON_GENERATION_NODE_INTENT_REVIEW",
    "K_LESSON_GENERATION_NODE_EXERCISE_AUTHOR",
    "K_LESSON_GENERATION_NODE_EXERCISE_COMPILE",
    "K_LESSON_GENERATION_NODE_EXERCISE_VERIFY",
    "K_LESSON_GENERATION_NODE_COVERAGE",
    "K_LESSON_GENERATION_NODE_HUMAN_GATE",
    "K_LESSON_GENERATION_NODE_FINALIZE",
]
