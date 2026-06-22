"""LangGraph Studio entry point. Exposes the compiled Lesson-QA graph as ``graph``.

Studio / ``langgraph dev`` loads this module and serves the graph declared in
``langgraph.json``. The graph is built with default collaborators (file-backed
loader/exporter). Studio now supplies persistence itself, so this entry point
must NOT provide a custom checkpointer.
"""

from __future__ import annotations

from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_qa_graph import (
    GraphDeps,
    build_lesson_qa_graph,
    default_exporter,
    default_loader,
    noop_fixer,
)


def _build_graph() -> object:
    deps = GraphDeps(
        loader=default_loader,
        exporter=default_exporter,
        fixer=noop_fixer,
        judge=noop_judge,
    )
    return build_lesson_qa_graph(deps)


graph = _build_graph()
