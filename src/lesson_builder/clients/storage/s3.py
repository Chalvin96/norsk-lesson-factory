"""Entry point: `S3StorageClient` (called by `application.operations.publish_release.upload_release_audio`).

The S3-compatible object adapter owns the raw provider operations: client
construction from the operator environment with bounded connect/read timeouts
and standard-mode retries, ``head_object`` translated to typed
missing/denied/verified outcomes, chunked object digests, and immutable
uploads with audio metadata. Whether an existing object may be skipped,
re-downloaded, or must fail closed is a release integrity decision and stays
in ``application.operations.publish_release``.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from typing import NamedTuple
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from lesson_builder.clients.storage.settings import K_S3_CONNECT_TIMEOUT_SECONDS
from lesson_builder.clients.storage.settings import K_S3_MAX_ATTEMPTS
from lesson_builder.clients.storage.settings import K_S3_READ_TIMEOUT_SECONDS

K_S3_MISSING_OBJECT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
K_S3_DENIED_OBJECT_CODES = frozenset({"403", "AccessDenied", "Forbidden"})


class S3ObjectMissingError(Exception):
    """The provider reported the object absent."""


class S3AccessDeniedError(Exception):
    """The provider denied permission to inspect the object; the code is the message."""


class S3ObjectHead(NamedTuple):
    """Verified object facts used by release integrity decisions."""

    content_length: int
    metadata: Mapping[str, str]


class S3Client(Protocol):
    """Minimal S3 client surface used by the storage adapter."""

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Return provider metadata for one object."""

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Return one object's streaming body."""

    def upload_file(self, filename: str, bucket: str, key: str, *, ExtraArgs: dict[str, Any]) -> None:
        """Upload one local file with provider metadata."""


class S3StorageClient:
    """Raw S3-compatible object operations behind the external release boundary."""

    def __init__(self, client: S3Client) -> None:
        self._client = client

    def head_object(self, *, bucket: str, key: str) -> S3ObjectHead:
        """Return verified object facts, or the typed missing/denied outcome."""
        try:
            head = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in K_S3_MISSING_OBJECT_CODES:
                raise S3ObjectMissingError(key) from exc
            if code in K_S3_DENIED_OBJECT_CODES:
                raise S3AccessDeniedError(code) from exc
            raise
        metadata = head.get("Metadata") or {}
        return S3ObjectHead(content_length=int(head.get("ContentLength", 0)), metadata=metadata)

    def object_digest(self, *, bucket: str, key: str) -> str:
        """Download one object once and return its SHA-256 hex digest."""
        hasher = hashlib.sha256()
        response = self._client.get_object(Bucket=bucket, Key=key)
        for chunk in response["Body"].iter_chunks(64 * 1024):
            hasher.update(chunk)
        return hasher.hexdigest()

    def upload_file(
        self,
        *,
        path: Path,
        bucket: str,
        key: str,
        content_type: str,
        cache_control: str,
        metadata: Mapping[str, str],
    ) -> None:
        """Upload one immutable object with its audio metadata and optional ACL."""
        extra_args: dict[str, Any] = {
            "ContentType": content_type,
            "CacheControl": cache_control,
            "Metadata": dict(metadata),
        }
        acl = os.environ.get("S3_OBJECT_ACL")
        if acl:
            extra_args["ACL"] = acl
        self._client.upload_file(str(path), bucket, key, ExtraArgs=extra_args)


def make_s3_client() -> S3StorageClient:
    """Create the S3-compatible client with bounded request retries/timeouts."""
    return S3StorageClient(
        boto3.client(
            "s3",
            endpoint_url=_required_env("S3_ENDPOINT_URL"),
            aws_access_key_id=_required_env("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=_required_env("S3_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(
                signature_version="s3v4",
                connect_timeout=K_S3_CONNECT_TIMEOUT_SECONDS,
                read_timeout=K_S3_READ_TIMEOUT_SECONDS,
                retries={"mode": "standard", "max_attempts": K_S3_MAX_ATTEMPTS},
                s3={"addressing_style": "path"},
            ),
        )
    )


def _required_env(name: str) -> str:
    """Return one required client setting without exposing its value."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"missing environment variable {name} (see .env.example)")
    return value


__all__ = [
    # Adapter entry points
    "S3StorageClient",
    "make_s3_client",
    # Typed outcomes consumed by release integrity decisions
    "S3AccessDeniedError",
    "S3ObjectHead",
    "S3ObjectMissingError",
]
