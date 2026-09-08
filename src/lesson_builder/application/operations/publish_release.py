"""Entry points: `publish_release` and `upload_release_audio` ship validated bytes.

Publication proves the supplied archive contains exactly the current
validated ``dist/`` inventory and bytes before anything external is called,
then uploads serving audio to S3-compatible storage and the complete archive
to a GitHub Release. Archive/source equality verification, audio object
integrity decisions, and destination reconciliation live here; archive
creation stays in the sibling ``package_distribution`` operation and the raw S3
and GitHub Release operations are injected from the
``lesson_builder.clients.storage`` and ``lesson_builder.clients.github``
adapters."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path
from pathlib import PurePosixPath

from dotenv import load_dotenv

from lesson_builder.application.operations.validate_distribution import validate_committed_distribution
from lesson_builder.application.operations.validate_distribution import validate_distribution
from lesson_builder.clients.github.releases import reconcile_release_asset as upload_release_asset
from lesson_builder.clients.storage.s3 import S3AccessDeniedError
from lesson_builder.clients.storage.s3 import S3ObjectHead
from lesson_builder.clients.storage.s3 import S3ObjectMissingError
from lesson_builder.clients.storage.s3 import S3StorageClient
from lesson_builder.clients.storage.s3 import make_s3_client
from lesson_builder.workspace.paths import WorkspacePaths

K_AUDIO_CONTENT_TYPE = "audio/wav"
K_AUDIO_CACHE_CONTROL = "public, max-age=31536000, immutable"
K_AUDIO_SHA256_METADATA_KEY = "sha256"
K_ARCHIVE_DIST_PREFIX = "dist/"


def publish_release(
    *,
    archive_path: Path,
    repo_root: Path,
    distribution_root: Path,
    upload_s3: bool = False,
    env_path: Path | None = None,
    github_repository: str | None = None,
    github_tag: str | None = None,
) -> dict[str, object]:
    """Publish serving audio to S3 and the complete archive to GitHub.

    Nothing external is called until the current committed distribution validates
    and the supplied archive is proven to contain exactly the same safe member
    inventory and bytes as the current ``dist/``.
    """
    archive = Path(archive_path)
    if not archive.is_file():
        raise FileNotFoundError(f"release archive not found at {archive}")
    if (github_repository is None) != (github_tag is None):
        raise ValueError("GitHub publication requires both repository and tag")
    if not upload_s3 and github_repository is None:
        raise ValueError("request at least one publication destination")
    root = Path(distribution_root)
    validation = validate_committed_distribution(Path(repo_root), distribution_root=root)
    _verify_archive_matches_distribution(archive, root)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    published: list[str] = []
    s3_result: dict[str, int] | None = None
    if upload_s3:
        s3_result = upload_release_audio(
            distribution_root=root,
            env_path=env_path,
        )
        published.append("s3_audio")
    if github_repository is not None and github_tag is not None:
        try:
            upload_release_asset(archive_path=archive, repository=github_repository, tag=github_tag)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GitHub release upload timed out; publication state is unknown") from exc
        published.append("github_release")
    return {
        "archive_path": str(archive),
        "sha256": digest,
        "published": published,
        "s3": s3_result,
        "validation": validation,
    }


def upload_release_audio(
    *,
    distribution_root: Path,
    env_path: Path | None = None,
    s3_client: S3StorageClient | None = None,
) -> dict[str, int]:
    """Upload missing release audio under each exported portable path.

    Every upload carries the audio digest as object metadata. An existing
    immutable object is skipped only when its content length and SHA-256
    metadata match the local asset, or once, when it predates metadata, when a
    one-time downloaded digest matches. Unknown or mismatched integrity fails
    closed instead of overwriting.
    """
    root = Path(distribution_root)
    validate_distribution(root)
    load_dotenv(env_path or Path.cwd() / ".env")
    bucket = _required_env("S3_BUCKET")
    client = s3_client or make_s3_client()
    dist = WorkspacePaths(root).dist_root
    uploaded = 0
    skipped = 0
    for key in _distribution_audio_paths(root):
        local = dist / key
        if _object_matches_integrity(client, bucket=bucket, key=key, local=local):
            skipped += 1
            continue
        client.upload_file(
            path=local,
            bucket=bucket,
            key=key,
            content_type=K_AUDIO_CONTENT_TYPE,
            cache_control=K_AUDIO_CACHE_CONTROL,
            metadata={K_AUDIO_SHA256_METADATA_KEY: _file_digest(local)},
        )
        uploaded += 1
    return {"uploaded": uploaded, "skipped": skipped}


def _verify_archive_matches_distribution(archive: Path, distribution_root: Path) -> None:
    """Require the archive to be exactly the current ``dist/`` inventory and bytes.

    Unsafe member paths, links, unexpected member types, duplicates, missing
    members, extras, and any byte drift from the validated release fail closed
    before a destination is called.
    """
    dist = WorkspacePaths(distribution_root).dist_root
    if not dist.is_dir():
        raise FileNotFoundError(f"release dist directory not found at {dist}")
    expected_files, expected_dirs = _archive_expected_members(dist)
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                if name in seen:
                    raise ValueError(f"archive repeats member: {name}")
                seen.add(name)
                _verify_archive_member(
                    tar=tar,
                    member=member,
                    dist=dist,
                    expected_files=expected_files,
                    expected_dirs=expected_dirs,
                )
    except tarfile.TarError as exc:
        raise ValueError(f"release archive is not a readable gzip/tar document: {exc}") from exc
    missing = sorted(expected_files - seen)
    if missing:
        raise ValueError("archive is missing dist/ members: " + ", ".join(missing))


def _archive_expected_members(dist: Path) -> tuple[set[str], set[str]]:
    """Return the expected archive file and directory names for ``dist``."""
    expected_files = {
        f"{K_ARCHIVE_DIST_PREFIX}{path.relative_to(dist).as_posix()}" for path in dist.rglob("*") if path.is_file()
    }
    expected_dirs = {K_ARCHIVE_DIST_PREFIX.rstrip("/")}
    expected_dirs.update(
        f"{K_ARCHIVE_DIST_PREFIX}{path.relative_to(dist).as_posix()}" for path in dist.rglob("*") if path.is_dir()
    )
    return expected_files, expected_dirs


def _verify_archive_member(
    *,
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    dist: Path,
    expected_files: set[str],
    expected_dirs: set[str],
) -> None:
    """Validate one archive member's path, type, inventory, and bytes."""
    name = member.name
    pure = PurePosixPath(name)
    if "\\" in name or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"archive member path is unsafe: {name!r}")
    if member.issym() or member.islnk():
        raise ValueError(f"archive member is a link: {name}")
    if member.isdir():
        if name not in expected_dirs:
            raise ValueError(f"archive contains an unexpected directory: {name}")
        return
    if not member.isfile():
        raise ValueError(f"archive member is not a regular file: {name}")
    if name not in expected_files:
        raise ValueError(f"archive contains a member absent from dist/: {name}")
    extracted = tar.extractfile(member)
    member_bytes = extracted.read() if extracted is not None else b""
    local_file = dist / PurePosixPath(name).relative_to("dist")
    if member_bytes != local_file.read_bytes():
        raise ValueError(f"archive member does not match the current dist/: {name}")


def _file_digest(path: Path) -> str:
    """Return the SHA-256 hex digest of one local file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object_matches_integrity(client: S3StorageClient, *, bucket: str, key: str, local: Path) -> bool:
    """Return whether the existing immutable object already matches local bytes."""
    head: S3ObjectHead
    try:
        head = client.head_object(bucket=bucket, key=key)
    except S3ObjectMissingError:
        return False
    except S3AccessDeniedError as exc:
        raise ValueError(
            f"cannot verify existing S3 object {key}: head_object returned {exc}; refusing to overwrite"
        ) from exc
    expected_size = local.stat().st_size
    if head.content_length != expected_size:
        raise ValueError(
            f"existing S3 object {key} has content length {head.content_length}, "
            f"expected {expected_size}; refusing to overwrite"
        )
    digest = _file_digest(local)
    existing_digest = head.metadata.get(K_AUDIO_SHA256_METADATA_KEY)
    if existing_digest == digest:
        return True
    if existing_digest is None:
        if client.object_digest(bucket=bucket, key=key) == digest:
            return True
        raise ValueError(f"existing S3 object {key} does not match local audio; refusing to overwrite")
    raise ValueError(f"existing S3 object {key} sha256 metadata does not match local audio; refusing to overwrite")


def _distribution_audio_paths(distribution_root: Path) -> list[str]:
    """Read validated packets directly from the lesson packet directory."""
    dist = WorkspacePaths(distribution_root).dist_root
    packet_paths = sorted((dist / "lessons").glob("*.json"), key=lambda path: path.name)
    paths: set[str] = set()
    for packet_path in packet_paths:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        paths.update(audio["path"] for audio in packet["media"]["audio"])
    return sorted(paths)


def _required_env(name: str) -> str:
    """Return one required publication setting without exposing its value."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"missing environment variable {name} (see .env.example)")
    return value


__all__ = ["publish_release", "upload_release_audio"]
