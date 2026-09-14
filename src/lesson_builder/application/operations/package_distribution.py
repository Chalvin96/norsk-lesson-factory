"""Entry point: `package_distribution` seals one validated distribution into an archive.

Packaging validates that a chosen distribution matches canonical source and turns
its complete ``dist/`` tree into reproducible, deterministically ordered
gzip/tar bytes at a caller-selected local path. Shipping those bytes to S3 or
GitHub is owned by the sibling publication operation.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

from lesson_builder.application.operations.validate_distribution import validate_committed_distribution
from lesson_builder.workspace.paths import WorkspacePaths


def package_distribution(
    *, repo_root: Path, distribution_root: Path, output_path: Path, include_audio: bool = True
) -> dict[str, object]:
    """Create one reproducible archive from a complete validated ``dist/``."""
    root = Path(distribution_root)
    validation = validate_committed_distribution(Path(repo_root), distribution_root=root)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _distribution_archive_bytes(WorkspacePaths(root).dist_root, include_audio=include_audio)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    return {
        "archive_path": str(destination),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "validation": validation,
        "include_audio": include_audio,
    }


def _distribution_archive_bytes(dist: Path, *, include_audio: bool = True) -> bytes:
    """Build stable gzip/tar bytes with normalized metadata and sorted paths."""
    if not dist.is_dir():
        raise FileNotFoundError(f"distribution dist directory not found at {dist}")
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        paths = [dist, *dist.rglob("*")]
        if not include_audio:
            paths = [path for path in paths if not _is_audio_path(path, dist)]
        for path in sorted(paths, key=lambda item: item.as_posix()):
            relative = Path("dist") / path.relative_to(dist)
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    return buffer.getvalue()


def _is_audio_path(path: Path, dist: Path) -> bool:
    """Return whether a distribution path belongs to serving audio."""
    try:
        relative = path.relative_to(dist)
    except ValueError:
        return False
    return relative.parts[:1] == ("audio",)


__all__ = ["package_distribution"]
