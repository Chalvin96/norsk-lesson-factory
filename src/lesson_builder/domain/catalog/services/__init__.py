"""Not a check itself — flat catalog services expose pure dependency policy.

``resolve_owner_references.build_owner_resolution`` and
``validate_dependency_graph.validate_dependency_graph`` /
``topological_order`` are deterministic helpers shared with the curriculum
planner. Model-assisted dependency review and approved-graph promotion are
application operations because they read or write repository files and call
providers. The typed dependency artifacts live in
``lesson_builder.domain.catalog.models``. Import each service module directly.
``normalization.normalize_text`` and ``normalization.normalize_slug`` provide
the pure label-comparison policy used by workflow nodes.
"""
