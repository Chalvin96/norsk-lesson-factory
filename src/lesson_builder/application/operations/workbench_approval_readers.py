"""Entry points: `latest_approval_record`, `approved_snapshot`, and `legacy_baseline_files` read approval evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

from lesson_builder.application.operations.workbench_records import StageApprovalRecord
from lesson_builder.application.operations.workbench_records import WorkbenchBaselines
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_APPROVAL_CACHE_SIZE
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_STAGES
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_transaction import contained_workbench_path


@dataclass(frozen=True)
class ApprovedSnapshot:
    """Verified human snapshot content, or an unavailable flag for a broken snapshot."""

    content: bytes | None
    unavailable: bool = False


def latest_approval_record(root: Path, lesson_id: str, stage: str) -> StageApprovalRecord | None:
    """Return the newest stage approval, including records carried in history."""
    return max(approval_records(root, lesson_id, stage), key=lambda record: record.approved_at, default=None)


def approval_records(root: Path, lesson_id: str, stage: str) -> list[StageApprovalRecord]:
    """Read stage approvals and their embedded history in approval-time order."""
    directory = contained_workbench_path(root, WorkspacePaths(root).workbench_approvals_root / lesson_id / stage)
    records = read_approval_records(directory, lesson_id, stage)
    history = [old for record in records for old in record.prior_approvals]
    return [*records, *history]


def read_approval_records(directory: Path, lesson_id: str, stage: str) -> list[StageApprovalRecord]:
    """Read approval metadata while ignoring source snapshots stored beside records."""
    records = []
    for path in directory.glob("*.yaml"):
        metadata = read_approval_yaml(path)
        if isinstance(metadata, dict):
            records.append(StageApprovalRecord.model_validate(metadata))
    if any(record.lesson_id != lesson_id or record.stage != stage for record in records):
        raise ValueError("approval record identity mismatch")
    return records


def approved_snapshot(root: Path, lesson_id: str, stage: Literal["lesson", "exercises"]) -> ApprovedSnapshot:
    """Read a verified human snapshot, or the legacy baseline when no record exists."""
    filename = "lesson.md" if stage == "lesson" else "exercises.yaml"
    record = latest_approval_record(root, lesson_id, stage)
    if record is None:
        baseline = legacy_baseline_files(root, lesson_id).get(filename)
        return ApprovedSnapshot(baseline.encode("utf-8") if baseline is not None else None)

    extension = "md" if stage == "lesson" else "yaml"
    path = WorkspacePaths(root).workbench_approvals_root / lesson_id / stage / f"{record.file_sha256}.{extension}"
    contained_workbench_path(root, path)
    if not path.is_file():
        return ApprovedSnapshot(None, unavailable=True)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != record.file_sha256:
        return ApprovedSnapshot(None, unavailable=True)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return ApprovedSnapshot(None, unavailable=True)
    return ApprovedSnapshot(content)


def legacy_baseline_files(root: Path, lesson_id: str) -> dict[str, str]:
    """Return the best matching legacy source snapshot for one lesson."""
    paths = WorkspacePaths(root)
    canonical = contained_workbench_path(root, paths.lessons_root / lesson_id)
    if not canonical.is_dir():
        return {}
    snapshots = contained_workbench_path(root, paths.lesson_approvals_root / lesson_id)
    pin = read_baselines(root).lessons.get(lesson_id)
    candidates: list[tuple[int, str, dict[str, str]]] = []
    for path in snapshots.glob("*/approval.yaml"):
        candidate = _legacy_baseline_candidate(
            root, lesson_id, canonical, path, pin.legacy_source_sha256 if pin else None
        )
        if candidate is not None:
            candidates.append(candidate)
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else {}


def read_baselines(root: Path) -> WorkbenchBaselines:
    """Read legacy baseline pins when present."""
    path = contained_workbench_path(root, WorkspacePaths(root).workbench_baselines)
    return WorkbenchBaselines.model_validate(read_approval_yaml(path)) if path.is_file() else WorkbenchBaselines()


def read_approval_yaml(path: Path) -> object:
    """Read approval YAML through the metadata-keyed cache."""
    metadata = path.stat()
    return _cached_approval_yaml(path, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)


def _legacy_baseline_candidate(
    root: Path, lesson_id: str, canonical: Path, approval_path: Path, pinned_digest: str | None
) -> tuple[int, str, dict[str, str]] | None:
    metadata = read_approval_yaml(approval_path)
    if not isinstance(metadata, dict) or metadata.get("lesson_id") != lesson_id:
        return None
    if pinned_digest and metadata.get("source_sha256") != pinned_digest:
        return None
    snapshot = contained_workbench_path(root, approval_path.parent / "source")
    if not all((snapshot / name).is_file() for _, name in K_APPLICATION_WORKBENCH_STAGES):
        return None
    files = {name: (snapshot / name).read_text(encoding="utf-8") for _, name in K_APPLICATION_WORKBENCH_STAGES}
    matching = sum(
        (canonical / name).is_file() and (canonical / name).read_text() == text for name, text in files.items()
    )
    return matching, str(metadata.get("approved_at", "")), files


@lru_cache(maxsize=K_APPLICATION_WORKBENCH_APPROVAL_CACHE_SIZE)
def _cached_approval_yaml(path: Path, modified: int, changed: int, size: int) -> object:
    return yaml.load(path.read_bytes(), Loader=yaml.CSafeLoader)
