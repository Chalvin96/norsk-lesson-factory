"""Entry points: `upload_release_asset` and `reconcile_release_asset`.

The GitHub adapter owns the ``gh release upload`` invocation: the exact
argument order, clobber semantics, and the configured timeout bound. Whether a
timeout leaves publication state unknown is an external release decision and
stays in ``application.operations.publish_release``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from lesson_builder.clients.github.settings import K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS


def upload_release_asset(*, archive_path: Path, repository: str, tag: str) -> None:
    """Upload one archive asset without allowing replacement."""
    subprocess.run(
        [
            "gh",
            "release",
            "upload",
            tag,
            str(archive_path),
            "--repo",
            repository,
        ],
        check=True,
        timeout=K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS,
    )


def reconcile_release_asset(*, archive_path: Path, repository: str, tag: str, _after_timeout: bool = False) -> str:
    """Create or reconcile an immutable GitHub release asset by SHA-256."""
    archive = Path(archive_path)
    name = archive.name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    try:
        result = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repository, "--json", "assets"],
            check=True,
            capture_output=True,
            text=True,
            timeout=K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS,
        )
        assets = json.loads(result.stdout).get("assets", [])
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or "").lower() if isinstance(exc.stderr, str) else ""
        if "not found" not in error and "could not find" not in error:
            raise
        subprocess.run(
            ["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--generate-notes"],
            check=True,
            timeout=K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS,
        )
        assets = []
    existing = next((asset for asset in assets if asset.get("name") == name), None)
    if existing:
        if existing.get("size") == archive.stat().st_size and existing.get("digest", "").endswith(digest):
            return "matched"
        raise ValueError(f"GitHub release asset {name!r} already exists with different bytes")
    try:
        upload_release_asset(archive_path=archive, repository=repository, tag=tag)
    except subprocess.TimeoutExpired:
        if _after_timeout:
            raise RuntimeError("GitHub release upload state remains unknown after reconciliation") from None
        return reconcile_release_asset(archive_path=archive, repository=repository, tag=tag, _after_timeout=True)
    return "uploaded"


__all__ = [
    # Adapter entry point
    "upload_release_asset",
    "reconcile_release_asset",
]
