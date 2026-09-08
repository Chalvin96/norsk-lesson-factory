"""Not a check itself — third-party object-storage client adapters.

``s3`` wraps the S3-compatible object API: client construction from the
operator environment with bounded retries/timeouts, head/digest/upload
operations, and typed missing/denied outcomes. Integrity decisions and
publication policy stay in ``application.operations.publish_release``.
"""
