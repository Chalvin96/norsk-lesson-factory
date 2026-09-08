"""Entry point: `S3StorageClient` behavior at the raw S3 boundary.

Offline: the adapter wraps an injected boto3-shaped fake. These tests own the
provider contract: head outcomes translated to typed missing/denied results,
chunked object digests, upload argument composition including the optional
ACL, and bounded client construction from the operator environment.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from lesson_builder.clients.storage.s3 import S3AccessDeniedError
from lesson_builder.clients.storage.s3 import S3ObjectHead
from lesson_builder.clients.storage.s3 import S3ObjectMissingError
from lesson_builder.clients.storage.s3 import S3StorageClient
from lesson_builder.clients.storage.s3 import make_s3_client
from lesson_builder.clients.storage.settings import K_S3_CONNECT_TIMEOUT_SECONDS
from lesson_builder.clients.storage.settings import K_S3_MAX_ATTEMPTS
from lesson_builder.clients.storage.settings import K_S3_READ_TIMEOUT_SECONDS
from tests.clients.storage.fakes import FakeRawS3


def test_s3_head_object_given_present_object_expect_verified_facts():
    raw = FakeRawS3(head={"ContentLength": 9, "Metadata": {"sha256": "a" * 64}})
    client = S3StorageClient(raw)

    head = client.head_object(bucket="lesson-media", key="audio/lessons/a/clip.wav")

    assert head == S3ObjectHead(content_length=9, metadata={"sha256": "a" * 64})
    assert raw.head_calls == ["audio/lessons/a/clip.wav"]


def test_s3_head_object_given_missing_codes_expect_typed_missing_error():
    for code in ("404", "NoSuchKey", "NotFound"):
        raw = FakeRawS3(head=ClientError({"Error": {"Code": code}}, "HeadObject"))
        client = S3StorageClient(raw)

        with pytest.raises(S3ObjectMissingError):
            client.head_object(bucket="lesson-media", key="gone")


def test_s3_head_object_given_denied_code_expect_typed_denied_error_with_code_message():
    raw = FakeRawS3(head=ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject"))
    client = S3StorageClient(raw)

    with pytest.raises(S3AccessDeniedError) as exc_info:
        client.head_object(bucket="lesson-media", key="locked")

    assert str(exc_info.value) == "AccessDenied"


def test_s3_head_object_given_unknown_provider_error_expect_propagation():
    raw = FakeRawS3(head=ClientError({"Error": {"Code": "InternalError"}}, "HeadObject"))
    client = S3StorageClient(raw)

    with pytest.raises(ClientError):
        client.head_object(bucket="lesson-media", key="broken")


def test_s3_object_digest_given_object_body_expect_sha256_hex_digest():
    payload = b"wav-bytes"
    raw = FakeRawS3(head={}, body=payload)
    client = S3StorageClient(raw)

    digest = client.object_digest(bucket="lesson-media", key="audio/lessons/a/clip.wav")

    assert digest == hashlib.sha256(payload).hexdigest()
    assert raw.get_calls == ["audio/lessons/a/clip.wav"]


def test_s3_upload_file_given_audio_metadata_expect_composed_upload_arguments(tmp_path: Path):
    local = tmp_path / "clip.wav"
    local.write_bytes(b"wav-bytes")
    raw = FakeRawS3(head={})
    client = S3StorageClient(raw)

    client.upload_file(
        path=local,
        bucket="lesson-media",
        key="audio/lessons/a/clip.wav",
        content_type="audio/wav",
        cache_control="public, max-age=31536000, immutable",
        metadata={"sha256": hashlib.sha256(b"wav-bytes").hexdigest()},
    )

    assert raw.uploads == [
        (
            str(local),
            "lesson-media",
            "audio/lessons/a/clip.wav",
            {
                "ContentType": "audio/wav",
                "CacheControl": "public, max-age=31536000, immutable",
                "Metadata": {"sha256": hashlib.sha256(b"wav-bytes").hexdigest()},
            },
        )
    ]


def test_s3_upload_file_given_acl_environment_expect_acl_argument(tmp_path: Path, monkeypatch):
    local = tmp_path / "clip.wav"
    local.write_bytes(b"wav-bytes")
    monkeypatch.setenv("S3_OBJECT_ACL", "public-read")
    raw = FakeRawS3(head={})
    client = S3StorageClient(raw)

    client.upload_file(
        path=local,
        bucket="lesson-media",
        key="audio/lessons/a/clip.wav",
        content_type="audio/wav",
        cache_control="public, max-age=31536000, immutable",
        metadata={},
    )

    assert raw.uploads[0][3]["ACL"] == "public-read"


def test_make_s3_client_given_environment_expect_bounded_client_configuration(monkeypatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://garage.example.test")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    captured: dict[str, object] = {}

    def fake_client(*args: object, **kwargs: object) -> FakeRawS3:
        captured.update(kwargs)
        return FakeRawS3(head={})

    monkeypatch.setattr("lesson_builder.clients.storage.s3.boto3.client", fake_client)

    make_s3_client()

    config = captured["config"]
    assert isinstance(config, Config)
    assert captured["endpoint_url"] == "https://garage.example.test"
    assert config.connect_timeout == K_S3_CONNECT_TIMEOUT_SECONDS
    assert config.read_timeout == K_S3_READ_TIMEOUT_SECONDS
    assert config.retries["mode"] == "standard"
    assert config.retries["max_attempts"] == K_S3_MAX_ATTEMPTS


def test_make_s3_client_given_missing_credentials_expect_value_error(monkeypatch) -> None:
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    with pytest.raises(ValueError, match="missing environment variable S3_ENDPOINT_URL"):
        make_s3_client()
