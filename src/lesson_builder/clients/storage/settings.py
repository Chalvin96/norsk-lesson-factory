"""Not a check itself — S3 storage adapter request policy constants."""

from __future__ import annotations

K_S3_CONNECT_TIMEOUT_SECONDS = 10
K_S3_READ_TIMEOUT_SECONDS = 60
K_S3_MAX_ATTEMPTS = 3

__all__ = [
    "K_S3_CONNECT_TIMEOUT_SECONDS",
    "K_S3_READ_TIMEOUT_SECONDS",
    "K_S3_MAX_ATTEMPTS",
]
