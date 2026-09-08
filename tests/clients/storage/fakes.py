"""Not a check itself — typed behavior fakes for S3 storage tests."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from lesson_builder.clients.storage.s3 import S3ObjectHead


class FakeStorageClient:
    """Behavior fake for the storage client seam used by publication tests."""

    def __init__(self, *, head: S3ObjectHead | Exception, digest: str = "0" * 64) -> None:
        self.head = head
        self.digest = digest
        self.uploads: list[dict[str, object]] = []
        self.head_calls: list[str] = []
        self.digest_calls: list[str] = []

    def head_object(self, *, bucket: str, key: str) -> S3ObjectHead:
        self.head_calls.append(key)
        if isinstance(self.head, Exception):
            raise self.head
        return self.head

    def object_digest(self, *, bucket: str, key: str) -> str:
        self.digest_calls.append(key)
        return self.digest

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
        self.uploads.append(
            {
                "path": str(path),
                "bucket": bucket,
                "key": key,
                "content_type": content_type,
                "cache_control": cache_control,
                "metadata": dict(metadata),
            }
        )


class FakeRawS3:
    """Behavior fake for the boto3-shaped S3 surface wrapped by the adapter."""

    def __init__(self, *, head: dict[str, object] | Exception, body: bytes | None = None) -> None:
        self.head = head
        self.body = body
        self.uploads: list[tuple[str, str, str, dict[str, object]]] = []
        self.head_calls: list[str] = []
        self.get_calls: list[str] = []

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.head_calls.append(str(kwargs.get("Key")))
        if isinstance(self.head, Exception):
            raise self.head
        return dict(self.head)

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_calls.append(str(kwargs.get("Key")))
        payload = self.body or b""

        class Body:
            def __init__(self, value: bytes) -> None:
                self._value = value

            def iter_chunks(self, chunk_size: int):
                yield self._value

        return {"Body": Body(payload)}

    def upload_file(self, filename, bucket, key, *, ExtraArgs) -> None:
        self.uploads.append((filename, bucket, key, ExtraArgs))
