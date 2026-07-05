"""Entry point: ``build_lesson_qa_graph``.

Lesson-QA back-half, wired as one LangGraph:

    load -> judge -> run_checks -> [route_after_aggregate]
                          ├─ no actionable issues          -> regression -> signoff -> human_gate
                          ├─ actionable issues, fixes left -> fix -> [route_after_fix] -> checks | human_gate
                          ├─ actionable issues, regen left -> regenerate -> checks
                          └─ exhausted            -> human_gate
    human_gate (interrupt) -> [route_after_human]
                          ├─ accept  -> export -> END
                          ├─ edit    -> run_checks (re-validate the human edit)
                          └─ defer   -> END (thread stays parked)

Nodes are pure functions of ``LessonQAState`` plus injected collaborators
(``GraphDeps``). The deterministic gate drives routing and author-budget; LLM
judges (pedagogy / objective_alignment / answer_valid) start advisory. The
``judge`` node runs once on entry and populates the review payloads; their
verdicts are folded into ``current_issues`` by every ``run_checks`` pass
(including the fix-loop and post-edit re-checks) as long as a review payload
is present in state. Issue-list findings stay advisory; promoted rubric-floor
findings become revision targets that drive the fix/regenerate loop without
remaining hard export blockers after the budget is exhausted.

The graph reuses ``gate_lesson_results`` from ``gate_manager`` and the advisory
``*_check_from_review`` surfaces. It does NOT rewrite any check and does NOT
parse exercise payloads inline — ``lesson_exercises.py`` stays the projection
boundary (used by ``answer_valid_check`` internally).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from lesson_builder.pipeline.graph_deps import (
    K_PARK_ACCEPTED,
    K_PARK_DEFERRED,
    K_PARK_PARKED,
    K_PARK_RUNNING,
    Exporter,
    Fixer,
    GraphDeps,
    Judge,
    LessonLoader,
    LessonLoadError,
    LoadedLesson,
    Rejudge,
    default_loader,
    noop_fixer,
)
from lesson_builder.pipeline.graph_nodes import (
    _park_thread_node,
    export_node,
    fix_node,
    human_gate_node,
    judge_node,
    load_node,
    regenerate_node,
    regression_node,
    run_checks_node,
    signoff_node,
)
from lesson_builder.pipeline.lesson_persistence import default_exporter
from lesson_builder.pipeline.state import (
    END_LITERAL,
    K_ROUTER_EXPORT,
    K_ROUTER_FIX,
    K_ROUTER_HUMAN,
    K_ROUTER_REGENERATE,
    K_ROUTER_REGRESSION,
    LessonQAState,
    route_after_aggregate,
    route_after_fix,
    route_after_human,
)

# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_lesson_qa_graph(
    deps: GraphDeps,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """Compile the Lesson-QA back-half graph.

    ``checkpointer`` enables resume/park. Without one the graph still runs
    end-to-end but cannot park at the human gate.
    """
    graph = StateGraph(LessonQAState)

    graph.add_node("load", lambda state: load_node(state, deps))
    graph.add_node("judge", lambda state: judge_node(state, deps))
    graph.add_node("run_checks", lambda state: run_checks_node(state, deps))
    graph.add_node("fix", lambda state: fix_node(state, deps))
    graph.add_node("regenerate", lambda state: regenerate_node(state, deps))
    graph.add_node("regression", lambda state: regression_node(state, deps))
    graph.add_node("signoff", lambda state: signoff_node(state, deps))
    graph.add_node("park_thread", _park_thread_node)
    graph.add_node("human_gate", lambda state: human_gate_node(state, deps))
    graph.add_node("export", lambda state: export_node(state, deps))

    graph.add_edge(START, "load")
    graph.add_edge("load", "judge")
    graph.add_edge("judge", "run_checks")
    graph.add_conditional_edges(
        "run_checks",
        route_after_aggregate,
        {
            K_ROUTER_REGRESSION: "regression",
            K_ROUTER_FIX: "fix",
            K_ROUTER_REGENERATE: "regenerate",
            K_ROUTER_HUMAN: "park_thread",
        },
    )
    graph.add_conditional_edges(
        "fix",
        route_after_fix,
        {"checks": "run_checks", K_ROUTER_HUMAN: "park_thread"},
    )
    graph.add_edge("regenerate", "run_checks")
    graph.add_edge("regression", "signoff")
    graph.add_edge("signoff", "park_thread")
    graph.add_edge("park_thread", "human_gate")
    graph.add_conditional_edges(
        "human_gate",
        route_after_human,
        {
            K_ROUTER_EXPORT: "export",
            "checks": "run_checks",
            K_ROUTER_HUMAN: "park_thread",
            END_LITERAL: END,
        },
    )
    graph.add_edge("export", END)

    return graph.compile(checkpointer=checkpointer)


__all__ = [
    "GraphDeps",
    "LoadedLesson",
    "LessonLoader",
    "Fixer",
    "Judge",
    "Rejudge",
    "Exporter",
    "build_lesson_qa_graph",
    "load_node",
    "judge_node",
    "run_checks_node",
    "fix_node",
    "regenerate_node",
    "regression_node",
    "signoff_node",
    "human_gate_node",
    "export_node",
    "default_loader",
    "default_exporter",
    "noop_fixer",
    "LessonLoadError",
    "K_PARK_RUNNING",
    "K_PARK_PARKED",
    "K_PARK_DEFERRED",
    "K_PARK_ACCEPTED",
]
