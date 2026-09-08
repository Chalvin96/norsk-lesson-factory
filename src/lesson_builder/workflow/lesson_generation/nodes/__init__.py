"""Entry point: ``build_lesson_generation_graph`` uses the workflow nodes.

Rich authoring stages are registered from the injected runner in the graph;
this package exports only nodes that add workflow-owned behavior.
"""

from lesson_builder.workflow.lesson_generation.nodes.finalize import finalize_node
from lesson_builder.workflow.lesson_generation.nodes.human_gate import human_gate_node
from lesson_builder.workflow.lesson_generation.nodes.prepare import prepare_node
from lesson_builder.workflow.lesson_generation.nodes.verify import verify_attestations_node

__all__ = [
    # workflow-owned graph nodes
    "verify_attestations_node",
    "finalize_node",
    "human_gate_node",
    "prepare_node",
]
