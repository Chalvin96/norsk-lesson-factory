"""Not a check itself — catalog services provide label and ordering policy.

``normalization.normalize_text`` and ``normalization.normalize_slug`` provide
the label comparisons used by catalog workflow nodes.
``validate_dependency_graph.validate_dependency_graph`` and
``topological_order`` provide deterministic ordering checks for curriculum.
Import each service module directly.
"""
