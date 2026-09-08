"""Behavior tests for dependency-graph validation and stable ordering."""

from lesson_builder.domain.catalog.models import DependencyEdge
from lesson_builder.domain.catalog.services.validate_dependency_graph import topological_order
from lesson_builder.domain.catalog.services.validate_dependency_graph import validate_dependency_graph


def test_validate_dependency_graph_given_required_cycle_expect_cycle_path() -> None:
    errors = validate_dependency_graph(
        ["one", "two"],
        [
            DependencyEdge(prerequisite_id="one", dependent_id="two", kind="required"),
            DependencyEdge(prerequisite_id="two", dependent_id="one", kind="required"),
        ],
    )

    assert any("required dependency cycle" in error for error in errors)
    assert any("one" in error and "two" in error for error in errors)


def test_validate_dependency_graph_given_unknown_required_endpoint_expect_reference_error() -> None:
    errors = validate_dependency_graph(
        ["known"],
        [DependencyEdge(prerequisite_id="known", dependent_id="missing", kind="required")],
    )

    assert errors == ["unknown dependent owner: missing"]


def test_topological_order_given_helpful_edge_expect_only_required_ordering() -> None:
    order = topological_order(
        ["dependent", "helpful", "required"],
        [
            DependencyEdge(prerequisite_id="required", dependent_id="dependent", kind="required"),
            DependencyEdge(prerequisite_id="helpful", dependent_id="dependent", kind="helpful"),
        ],
        cefr_by_owner={"dependent": "A1", "helpful": "A1", "required": "A1"},
        kind_by_owner={"dependent": "grammar", "helpful": "grammar", "required": "grammar"},
        kind_order=("grammar",),
    )

    required_only_order = topological_order(
        ["dependent", "helpful", "required"],
        [DependencyEdge(prerequisite_id="required", dependent_id="dependent", kind="required")],
        cefr_by_owner={"dependent": "A1", "helpful": "A1", "required": "A1"},
        kind_by_owner={"dependent": "grammar", "helpful": "grammar", "required": "grammar"},
        kind_order=("grammar",),
    )

    assert order.index("required") < order.index("dependent")
    assert order == required_only_order
