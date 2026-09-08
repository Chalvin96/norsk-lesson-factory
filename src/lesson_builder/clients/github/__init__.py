"""Not a check itself — third-party GitHub client adapters.

``releases`` wraps the ``gh`` release-upload command behind one function with
the configured timeout bound. Publication policy, archive validation, and
result composition stay in ``application.operations.publish_release``.
"""
