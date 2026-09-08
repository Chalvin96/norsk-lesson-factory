"""Entry points: ``create_lesson_approval`` and ``read_lesson_approval``."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Literal
from typing import Self

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.workspace.paths import WorkspacePaths

K_APPROVAL_FILES = ("plan.md", "lesson.md", "exercises.yaml")
K_SHA256_PREFIX = "sha256:"


class LessonApprovalError(ValueError):
    """An immutable approval is missing, malformed, or conflicts."""


class ApprovalRecord(BaseModel):
    """Typed, closed-world metadata for one canonical immutable approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    approval_id: str
    lesson_id: str
    decision: Literal["accepted"]
    source_sha256: str
    replaces_source_sha256: str | None
    curriculum_slot_sha256: str
    evidence_sha256: str
    reviewer: str = Field(min_length=1)
    approved_at: datetime

    @field_validator("source_sha256", "replaces_source_sha256", "curriculum_slot_sha256", "evidence_sha256")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is not None and (not value.startswith(K_SHA256_PREFIX) or len(value) != len(K_SHA256_PREFIX) + 64):
            raise ValueError("digest must be sha256 followed by 64 hexadecimal characters")
        if value is not None:
            int(value.removeprefix(K_SHA256_PREFIX), 16)
        return value

    @field_validator("approved_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        validate_slug(self.lesson_id)
        if self.approval_id != f"lesson:{self.lesson_id}:{self.source_sha256}":
            raise ValueError("approval_id does not match lesson and source digest")
        return self


def create_lesson_approval(
    *,
    repo_root: Path,
    source_dir: Path,
    lesson_id: str,
    curriculum_slot_sha256: str | None = None,
    evidence_sha256: str | None = None,
    reviewer: str = "catalog-package-operator",
) -> Path:
    """Create or verify one create-only approval containing exact source bytes."""
    if curriculum_slot_sha256 is None or evidence_sha256 is None:
        raise LessonApprovalError("canonical approval requires curriculum slot and evidence digests")
    root = Path(repo_root)
    paths = WorkspacePaths(root)
    source = Path(source_dir)
    validate_slug(lesson_id)
    files = _read_source_files(source)
    digest = _hash_files(files)
    canonical = paths.lessons_root / lesson_id
    predecessor = _current_source_hash(canonical)
    if canonical.exists() and predecessor is None:
        raise LessonApprovalError(f"canonical lesson is incomplete: {canonical}")
    approval_dir = paths.lesson_approvals_root / lesson_id / digest.removeprefix(K_SHA256_PREFIX)
    existing = approval_dir / "approval.yaml"
    if predecessor == digest and existing.exists():
        predecessor = _existing_predecessor(root, approval_dir, existing)
    record = ApprovalRecord(
        schema_version="1",
        approval_id=f"lesson:{lesson_id}:{digest}",
        lesson_id=lesson_id,
        decision="accepted",
        source_sha256=digest,
        replaces_source_sha256=predecessor,
        curriculum_slot_sha256=curriculum_slot_sha256,
        evidence_sha256=evidence_sha256,
        reviewer=reviewer,
        approved_at=datetime.now(UTC),
    )
    if existing.exists():
        _verify_existing_approval(root, approval_dir, existing, record)
        return approval_dir
    return _write_approval(
        approval_dir=approval_dir,
        files=files,
        record=record,
        retry=lambda: create_lesson_approval(
            repo_root=root,
            source_dir=source,
            lesson_id=lesson_id,
            curriculum_slot_sha256=curriculum_slot_sha256,
            evidence_sha256=evidence_sha256,
            reviewer=reviewer,
        ),
    )


def read_lesson_approval(repo_root: Path, approval_path: Path) -> ApprovalRecord:
    """Read and validate approval metadata and its exact three-file snapshot."""
    root = Path(repo_root)
    path = Path(approval_path)
    if not path.is_absolute():
        path = root / path
    payload = yaml.safe_load((path / "approval.yaml").read_text(encoding="utf-8"))
    record = ApprovalRecord.model_validate(payload)
    expected_root = WorkspacePaths(root).lesson_approvals_root.resolve()
    path.resolve().relative_to(expected_root)
    if path.parent.name != record.lesson_id or path.name != record.source_sha256.removeprefix(K_SHA256_PREFIX):
        raise LessonApprovalError("approval directory identity does not match metadata")
    snapshot = _read_exact_snapshot(path)
    if _hash_files(snapshot) != record.source_sha256:
        raise LessonApprovalError("approval snapshot digest does not match metadata")
    return record


def _existing_predecessor(root: Path, approval_dir: Path, existing: Path) -> str | None:
    """Read the predecessor digest from an approval that already exists."""
    try:
        return read_lesson_approval(root, approval_dir).replaces_source_sha256
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise LessonApprovalError(f"invalid existing approval: {existing}: {exc}") from exc


def _verify_existing_approval(
    root: Path,
    approval_dir: Path,
    existing: Path,
    record: ApprovalRecord,
) -> None:
    """Require an existing approval identity to match the new metadata."""
    try:
        prior = read_lesson_approval(root, approval_dir)
        if prior.model_copy(update={"approved_at": record.approved_at}) != record:
            raise LessonApprovalError(f"approval identity already exists with different metadata: {existing}")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        if isinstance(exc, LessonApprovalError):
            raise LessonApprovalError(f"approval identity already exists with different source: {existing}") from exc
        raise LessonApprovalError(f"invalid existing approval: {existing}: {exc}") from exc


def _write_approval(
    *,
    approval_dir: Path,
    files: dict[str, bytes],
    record: ApprovalRecord,
    retry: Callable[[], Path],
) -> Path:
    """Write an approval snapshot through a create-only staging rename."""
    approval_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = approval_dir.parent / f".{approval_dir.name}.tmp-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    (staging / "source").mkdir()
    for name, content in files.items():
        (staging / "source" / name).write_bytes(content)
    (staging / "approval.yaml").write_text(
        yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    try:
        staging.rename(approval_dir)
    except FileExistsError:
        shutil.rmtree(staging)
        return retry()
    return approval_dir


def _read_exact_files(source: Path) -> dict[str, bytes]:
    if not source.is_dir() or {path.name for path in source.iterdir()} != set(K_APPROVAL_FILES):
        raise LessonApprovalError("approval source must contain exactly plan.md, lesson.md, and exercises.yaml")
    return {name: (source / name).read_bytes() for name in K_APPROVAL_FILES}


def _read_source_files(source: Path) -> dict[str, bytes]:
    if not source.is_dir() or any(not (source / name).is_file() for name in K_APPROVAL_FILES):
        raise LessonApprovalError("approval source is missing one of the three authored files")
    return {name: (source / name).read_bytes() for name in K_APPROVAL_FILES}


def _read_exact_snapshot(approval: Path) -> dict[str, bytes]:
    if {path.name for path in approval.iterdir()} != {"approval.yaml", "source"}:
        raise LessonApprovalError("approval inventory must contain only approval.yaml and source")
    return _read_exact_files(approval / "source")


def _hash_files(files: dict[str, bytes]) -> str:
    hasher = hashlib.sha256()
    for name in K_APPROVAL_FILES:
        hasher.update(name.encode())
        hasher.update(b"\0")
        hasher.update(files[name])
    return f"{K_SHA256_PREFIX}{hasher.hexdigest()}"


def _current_source_hash(path: Path) -> str | None:
    if not path.is_dir():
        return None
    try:
        return _hash_files(_read_exact_files(path))
    except (OSError, LessonApprovalError):
        return None


__all__ = ["ApprovalRecord", "LessonApprovalError", "create_lesson_approval", "read_lesson_approval"]
