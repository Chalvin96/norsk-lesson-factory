"""Entry points: `load_batch`, `write_batch_result`, and `build_slot_run_id`.

This workflow-owned module isolates batch aggregate I/O and stable per-slot
run identifiers from generation scheduling. It only serializes typed scratch
artifacts; lesson authoring and provider calls remain in the caller. The batch
workflow calls these helpers for checkpoint persistence and per-slot identity.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from pathlib import Path

import yaml

from lesson_builder.workflow.lesson_generation.models import LessonBatchResult

try:
    import fcntl
except ImportError:  # pragma: no cover - supported deployments run on POSIX
    fcntl = None  # type: ignore[assignment]


def load_batch(path: Path) -> LessonBatchResult:
    """Load a prior aggregate without changing its per-owner diagnostics."""
    if not path.exists():
        raise FileNotFoundError(f"lesson batch not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("lesson batch must be a YAML mapping")
    return LessonBatchResult.model_validate(payload)


def write_batch_result(output_root: Path, result: LessonBatchResult) -> None:
    """Persist one reviewable scratch aggregate through a temporary file."""
    if fcntl is None:
        raise RuntimeError("batch artifact locking requires a POSIX platform")
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".batch.yaml.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            temporary_path = output_root / ".batch.yaml.tmp"
            temporary_path.write_text(
                yaml.safe_dump(result.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            temporary_path.replace(output_root / "batch.yaml")
        finally:
            with suppress(OSError):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def build_slot_run_id(batch_id: str, catalog_id: str) -> str:
    """Create a stable, collision-resistant lesson-package run ID."""
    digest = hashlib.sha256(f"{batch_id}:{catalog_id}".encode()).hexdigest()[:10]
    prefix = f"{batch_id}-{catalog_id}"
    value = f"{prefix[: 64 - len(digest) - 1]}-{digest}"
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", value):
        raise ValueError(f"unsafe lesson run ID: {value!r}")
    return value


__all__ = ["build_slot_run_id", "load_batch", "write_batch_result"]
