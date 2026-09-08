"""Not a check itself — package containing catalog graph node implementations.

Each registered node lives under ``nodes/``: single-file nodes use
``nodes/<stage>.py``, and a node that owns catalog-review prompt assembly keeps
a ``nodes/<stage>/`` package with its ``node.py`` and ``prompt.py``.
Helpers shared by more than one node stay in ``node_support.py`` so no single
node module owns shared behavior. ``build_catalog_graph`` imports each
implementation directly so importing a prompt does not initialize every graph
node.
"""
