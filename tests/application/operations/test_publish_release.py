"""Entry point: `publish_release` / `upload_release_audio` publication orchestration.

Only the external boundaries are faked: the S3 storage client and the GitHub
release adapter. Everything else (validation, archive verification, integrity
decisions, result composition) runs for real against temporary releases.
Provider command and client-construction behavior lives with the adapter tests
under ``tests/clients``.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from lesson_builder.application.operations.package_distribution import package_distribution
from lesson_builder.application.operations.publish_release import publish_release
from lesson_builder.application.operations.publish_release import upload_release_audio
from lesson_builder.clients.storage.s3 import S3AccessDeniedError
from lesson_builder.clients.storage.s3 import S3ObjectHead
from lesson_builder.clients.storage.s3 import S3ObjectMissingError
from tests.clients.storage.fakes import FakeStorageClient


def _write_release(root: Path) -> Path:
    """Write one minimal provider-free release and return its root."""
    dist = root / "dist"
    (dist / "schema").mkdir(parents=True)
    (dist / "catalog.json").write_text('{"lessons": []}\n', encoding="utf-8")
    (dist / "schema" / "lesson.schema.json").write_text("{}\n", encoding="utf-8")
    return root


def _patch_validation(monkeypatch) -> None:
    """Stub committed-release validation in both packaging and publication."""

    def stub(repo_root, *, distribution_root) -> dict[str, int]:
        return {"lesson_count": 0, "audio_file_count": 0}

    monkeypatch.setattr(
        "lesson_builder.application.operations.package_distribution.validate_committed_distribution", stub
    )
    monkeypatch.setattr("lesson_builder.application.operations.publish_release.validate_committed_distribution", stub)


def _patch_upload(monkeypatch, result: dict[str, int]) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_upload(**kwargs: object) -> dict[str, int]:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_audio",
        fake_upload,
    )
    return calls


def _patch_gh(monkeypatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def fake_upload(*, archive_path: Path, repository: str, tag: str) -> None:
        calls.append({"archive_path": str(archive_path), "repository": repository, "tag": tag})

    monkeypatch.setattr("lesson_builder.application.operations.publish_release.upload_release_asset", fake_upload)
    return calls


def _build_archive(release_root: Path, output: Path, monkeypatch) -> Path:
    _patch_validation(monkeypatch)
    package_distribution(
        repo_root=release_root,
        distribution_root=release_root,
        output_path=output,
    )
    return output


def test_publish_release_given_matching_archive_expect_destinations_called(tmp_path: Path, monkeypatch) -> None:
    release_root = _write_release(tmp_path / "release")
    archive = _build_archive(release_root, tmp_path / "lessons.tar.gz", monkeypatch)
    upload_calls = _patch_upload(monkeypatch, {"uploaded": 2, "skipped": 1})
    gh_calls = _patch_gh(monkeypatch)

    result = publish_release(
        archive_path=archive,
        repo_root=tmp_path,
        distribution_root=release_root,
        upload_s3=True,
        github_repository="owner/repo",
        github_tag="lessons-2026.08.23",
    )

    assert result["published"] == ["s3_audio", "github_release"]
    assert result["s3"] == {"uploaded": 2, "skipped": 1}
    assert upload_calls == [{"distribution_root": release_root, "env_path": None}]
    assert gh_calls == [{"archive_path": str(archive), "repository": "owner/repo", "tag": "lessons-2026.08.23"}]


def test_publish_release_given_github_upload_timeout_expect_unknown_publication_state(
    tmp_path: Path, monkeypatch
) -> None:
    release_root = _write_release(tmp_path / "release")
    archive = _build_archive(release_root, tmp_path / "lessons.tar.gz", monkeypatch)
    calls: list[dict[str, str]] = []

    def timeout_upload(*, archive_path: Path, repository: str, tag: str) -> None:
        calls.append({"archive_path": str(archive_path), "repository": repository, "tag": tag})
        raise subprocess.TimeoutExpired(["gh", "release", "upload"], 300)

    monkeypatch.setattr("lesson_builder.application.operations.publish_release.upload_release_asset", timeout_upload)

    with pytest.raises(RuntimeError, match="publication state is unknown"):
        publish_release(
            archive_path=archive,
            repo_root=tmp_path,
            distribution_root=release_root,
            github_repository="owner/repo",
            github_tag="lessons-2026.08.23",
        )

    assert calls == [{"archive_path": str(archive), "repository": "owner/repo", "tag": "lessons-2026.08.23"}]


def test_publish_release_given_stale_archive_expect_refusal_before_destinations(tmp_path: Path, monkeypatch) -> None:
    release_root = _write_release(tmp_path / "release")
    archive = _build_archive(release_root, tmp_path / "lessons.tar.gz", monkeypatch)
    (release_root / "dist" / "catalog.json").write_text('{"stale": true}\n', encoding="utf-8")
    upload_calls = _patch_upload(monkeypatch, {"uploaded": 0, "skipped": 0})
    gh_calls = _patch_gh(monkeypatch)

    with pytest.raises(ValueError, match="does not match the current dist/"):
        publish_release(
            archive_path=archive,
            repo_root=tmp_path,
            distribution_root=release_root,
            upload_s3=True,
            github_repository="owner/repo",
            github_tag="lessons-2026.08.23",
        )

    assert upload_calls == []
    assert gh_calls == []


def _tampered_archive(release_root: Path, monkeypatch, *, mutate) -> Path:
    archive = _build_archive(release_root, release_root.parent / "lessons.tar.gz", monkeypatch)
    source = _build_archive(release_root, release_root.parent / "source.tar.gz", monkeypatch)
    buffer = io.BytesIO()
    with (
        tarfile.open(source, "r:gz") as reader,
        tarfile.open(fileobj=buffer, mode="w:gz") as writer,
    ):
        for member in reader.getmembers():
            extracted = reader.extractfile(member) if member.isfile() else None
            mutate(member, extracted, writer)
    archive_data = buffer.getvalue()
    archive.write_bytes(archive_data)
    return archive


def test_publish_release_given_absolute_member_expect_refusal_before_destinations(tmp_path: Path, monkeypatch) -> None:
    release_root = _write_release(tmp_path / "release")

    def add_absolute(member, extracted, writer) -> None:
        if member.isfile():
            writer.addfile(member, extracted)
        else:
            unsafe = tarfile.TarInfo("/etc/evil.txt")
            unsafe.size = 0
            writer.addfile(unsafe)

    archive = _tampered_archive(release_root, monkeypatch, mutate=add_absolute)
    upload_calls = _patch_upload(monkeypatch, {"uploaded": 0, "skipped": 0})
    gh_calls = _patch_gh(monkeypatch)

    with pytest.raises(ValueError, match="unsafe"):
        publish_release(
            archive_path=archive,
            repo_root=tmp_path,
            distribution_root=release_root,
            github_repository="owner/repo",
            github_tag="lessons-2026.08.23",
        )

    assert upload_calls == []
    assert gh_calls == []


def test_publish_release_given_duplicate_member_expect_refusal_before_destinations(tmp_path: Path, monkeypatch) -> None:
    release_root = _write_release(tmp_path / "release")

    def duplicate_catalog(member, extracted, writer) -> None:
        payload = extracted.read() if extracted is not None else None
        writer.addfile(member, io.BytesIO(payload) if payload is not None else None)
        if member.name == "dist/catalog.json":
            writer.addfile(member, io.BytesIO(payload) if payload is not None else None)

    archive = _tampered_archive(release_root, monkeypatch, mutate=duplicate_catalog)
    upload_calls = _patch_upload(monkeypatch, {"uploaded": 0, "skipped": 0})
    gh_calls = _patch_gh(monkeypatch)

    with pytest.raises(ValueError, match="repeats member"):
        publish_release(
            archive_path=archive,
            repo_root=tmp_path,
            distribution_root=release_root,
            github_repository="owner/repo",
            github_tag="lessons-2026.08.23",
        )

    assert upload_calls == []
    assert gh_calls == []


def test_publish_release_given_missing_member_expect_refusal_before_destinations(tmp_path: Path, monkeypatch) -> None:
    release_root = _write_release(tmp_path / "release")

    def drop_schema(member, extracted, writer) -> None:
        if member.name == "dist/schema/lesson.schema.json":
            return
        writer.addfile(member, extracted)

    archive = _tampered_archive(release_root, monkeypatch, mutate=drop_schema)
    upload_calls = _patch_upload(monkeypatch, {"uploaded": 0, "skipped": 0})
    gh_calls = _patch_gh(monkeypatch)

    with pytest.raises(ValueError, match="missing dist/ members"):
        publish_release(
            archive_path=archive,
            repo_root=tmp_path,
            distribution_root=release_root,
            github_repository="owner/repo",
            github_tag="lessons-2026.08.23",
        )

    assert upload_calls == []
    assert gh_calls == []


def _audio_release(tmp_path: Path) -> tuple[Path, Path]:
    release = tmp_path / "release"
    packet_path = release / "dist" / "lessons" / "present_tense.json"
    audio_path = release / "dist" / "audio" / "lessons" / "present_tense" / "clip.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"wav-bytes")
    (release / "dist" / "catalog.json").write_text('{"lessons": []}', encoding="utf-8")
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        json.dumps({"media": {"audio": [{"path": "audio/lessons/present_tense/clip.wav"}]}}),
        encoding="utf-8",
    )
    return release, audio_path


def _patch_audio_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.validate_distribution",
        lambda root: {"audio_file_count": 1},
    )


def test_upload_release_audio_given_missing_objects_expect_upload_with_digest_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    release, audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(head=S3ObjectMissingError("audio/lessons/present_tense/clip.wav"))

    result = upload_release_audio(distribution_root=release, s3_client=client)

    assert result == {"uploaded": 1, "skipped": 0}
    assert client.uploads == [
        {
            "path": str(audio_path),
            "bucket": "lesson-media",
            "key": "audio/lessons/present_tense/clip.wav",
            "content_type": "audio/wav",
            "cache_control": "public, max-age=31536000, immutable",
            "metadata": {"sha256": hashlib.sha256(b"wav-bytes").hexdigest()},
        }
    ]


def test_upload_release_audio_given_matching_metadata_expect_skipped(tmp_path: Path, monkeypatch) -> None:
    release, _audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(
        head=S3ObjectHead(content_length=9, metadata={"sha256": hashlib.sha256(b"wav-bytes").hexdigest()})
    )

    result = upload_release_audio(distribution_root=release, s3_client=client)

    assert result == {"uploaded": 0, "skipped": 1}
    assert client.uploads == []
    assert client.digest_calls == []


def test_upload_release_audio_given_mismatched_metadata_expect_fail_closed(tmp_path: Path, monkeypatch) -> None:
    release, _audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(head=S3ObjectHead(content_length=9, metadata={"sha256": "0" * 64}))

    with pytest.raises(ValueError, match="sha256 metadata does not match"):
        upload_release_audio(distribution_root=release, s3_client=client)

    assert client.uploads == []


def test_upload_release_audio_given_size_mismatch_expect_fail_closed(tmp_path: Path, monkeypatch) -> None:
    release, _audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(
        head=S3ObjectHead(content_length=12, metadata={"sha256": hashlib.sha256(b"wav-bytes").hexdigest()})
    )

    with pytest.raises(ValueError, match="content length 12"):
        upload_release_audio(distribution_root=release, s3_client=client)

    assert client.uploads == []


def test_upload_release_audio_given_legacy_object_without_metadata_expect_compatibility_digest(
    tmp_path: Path, monkeypatch
) -> None:
    release, _audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(
        head=S3ObjectHead(content_length=9, metadata={}),
        digest=hashlib.sha256(b"wav-bytes").hexdigest(),
    )

    result = upload_release_audio(distribution_root=release, s3_client=client)

    assert result == {"uploaded": 0, "skipped": 1}
    assert client.digest_calls == ["audio/lessons/present_tense/clip.wav"]
    assert client.uploads == []


def test_upload_release_audio_given_unknown_head_response_expect_fail_closed(tmp_path: Path, monkeypatch) -> None:
    release, _audio_path = _audio_release(tmp_path)
    _patch_audio_validation(monkeypatch)
    monkeypatch.setenv("S3_BUCKET", "lesson-media")
    client = FakeStorageClient(head=S3AccessDeniedError("AccessDenied"))

    with pytest.raises(ValueError, match="refusing to overwrite"):
        upload_release_audio(distribution_root=release, s3_client=client)
    assert client.uploads == []
