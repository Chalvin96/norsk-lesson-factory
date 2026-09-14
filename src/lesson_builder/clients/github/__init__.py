"""Not a check itself — third-party GitHub client adapters.

``releases`` wraps the ``gh`` release upload, reconcile, download, and tag-resolution commands
behind one function each with configured timeout bounds. Publication policy,
archive validation, and result composition stay in the application operations
that call the adapter.
"""
