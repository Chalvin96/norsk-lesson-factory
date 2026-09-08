"""Entry points: immutable source promotion and compatibility batch orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal
from typing import TextIO

import yaml

from lesson_builder.application.operations.export_distribution import create_lesson_packet_from_source
from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.workflow.lesson_generation.approval import ApprovalRecord
from lesson_builder.workflow.lesson_generation.approval import LessonApprovalError
from lesson_builder.workflow.lesson_generation.approval import read_lesson_approval
from lesson_builder.workflow.lesson_generation.artifacts import build_approval_receipt_path
from lesson_builder.workflow.lesson_generation.batch_plan import load_plan as load_lesson_plan
from lesson_builder.workflow.lesson_generation.batch_plan import render_plan as render_lesson_plan
from lesson_builder.workflow.lesson_generation.models import LessonBatchResult
from lesson_builder.workflow.lesson_generation.models import LessonPromotionOutcome
from lesson_builder.workflow.lesson_generation.models import LessonPromotionResult
from lesson_builder.workflow.lesson_generation.models import LessonResult
from lesson_builder.workflow.lesson_generation.stage_attestations import read_stage_attestations
from lesson_builder.workflow.lesson_generation.stage_attestations import summary_statuses
from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations
from lesson_builder.workspace.paths import WorkspacePaths

K_PROMOTION_AUTHORING_FILES = ("plan.md", "lesson.md", "exercises.yaml")
K_PROMOTION_HASH_ALGORITHM = "sha256"
K_PROMOTION_LOCK_ROOT = Path("store") / "locks" / "lesson-promotion"


class LessonPromotionError(ValueError):
    """A batch is not safe to promote or one package failed validation."""

    def __init__(self, message: str, *, result: LessonPromotionResult | None = None) -> None:
        super().__init__(message)
        self.result = result


def promote_lesson_approval(*, repo_root: Path, approval_path: Path) -> str:
    """Resumably reconcile one immutable approval into canonical lesson source."""
    root = Path(repo_root)
    approval = _resolve_approval_path(root, approval_path)
    metadata, files = _load_approval_for_promotion(root, approval)
    expected = metadata.source_sha256
    if _hash_files(files) != expected:
        raise LessonPromotionError(f"approval source digest does not match metadata: {approval}")
    lock_path = root / K_PROMOTION_LOCK_ROOT / f"{metadata.lesson_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        _lock_exclusively(lock)
        return _promote_locked(root, metadata, files)


def promote_lesson_batch(
    *,
    repo_root: Path,
    source_batch_path: Path,
    auto_approve: bool,
    catalog_ids: list[str] | None = None,
    replace: bool = False,
) -> LessonPromotionResult:
    """Promote selected packages independently, then assemble ``dist/`` once."""
    if not auto_approve:
        raise LessonPromotionError("promotion requires explicit --auto-approve")
    root = Path(repo_root)
    batch_path = root / source_batch_path
    batch = _load_batch(batch_path)
    if batch.status != "completed":
        raise LessonPromotionError(
            f"batch {batch.batch_id!r} is {batch.status!r}; only completed batches may be promoted"
        )
    selected = _select_results(batch.results, catalog_ids)
    if not selected:
        raise LessonPromotionError("no lesson packages selected for promotion")
    _validate_results_are_parked(selected)
    _validate_current_slot_plans(root, batch, selected)

    prepared: list[_PreparedPackage] = []
    unchanged_ids: set[str] = set()
    approval_paths: list[Path] = []
    outcomes: list[LessonPromotionOutcome] = []
    for result in selected:
        try:
            package, approval_path, outcome = _promote_batch_item(root, result, replace=replace)
            prepared.append(package)
            if outcome.status == "unchanged":
                unchanged_ids.add(package.catalog_id)
            if approval_path is not None:
                approval_paths.append(approval_path)
            outcomes.append(outcome)
        except Exception as exc:  # noqa: BLE001 - retain prior per-lesson outcomes
            catalog_id = result.catalog_id
            outcomes.append(LessonPromotionOutcome(catalog_id=catalog_id, status="failed", error=str(exc)))
            partial = _promotion_result(
                batch,
                source_batch_path,
                prepared,
                approval_paths,
                unchanged_ids,
                outcomes,
                root,
                status="incomplete",
                distribution_status="not_attempted",
            )
            raise LessonPromotionError(f"{catalog_id}: promotion failed: {exc}", result=partial) from exc
    try:
        export_distribution(root)
    except Exception as exc:  # noqa: BLE001 - distribution is downstream and retryable
        partial = _promotion_result(
            batch,
            source_batch_path,
            prepared,
            approval_paths,
            unchanged_ids,
            outcomes,
            root,
            status="distribution_failed",
            distribution_status="failed",
        )
        raise LessonPromotionError(
            f"distribution export failed after lesson promotions: {exc}", result=partial
        ) from exc
    _write_legacy_approval_receipt(root, batch.batch_id, approval_paths)

    return _promotion_result(batch, source_batch_path, prepared, approval_paths, unchanged_ids, outcomes, root)


def _resolve_approval_path(root: Path, approval_path: Path) -> Path:
    """Resolve an approval path and keep it inside the canonical root."""
    approval = Path(approval_path)
    if not approval.is_absolute():
        approval = root / approval
    approval_root = WorkspacePaths(root).lesson_approvals_root.resolve()
    try:
        approval = approval.resolve()
        approval.relative_to(approval_root)
    except ValueError as exc:
        raise LessonPromotionError("approval path is outside canonical approval root") from exc
    return approval


def _load_approval_for_promotion(root: Path, approval: Path) -> tuple[ApprovalRecord, dict[str, bytes]]:
    """Read one immutable approval and its exact source snapshot."""
    try:
        metadata = read_lesson_approval(root, approval)
        files = {name: (approval / "source" / name).read_bytes() for name in K_PROMOTION_AUTHORING_FILES}
    except (OSError, TypeError, ValueError, yaml.YAMLError, LessonApprovalError) as exc:
        raise LessonPromotionError(f"invalid immutable lesson approval: {approval}: {exc}") from exc
    return metadata, files


def _lock_exclusively(lock: TextIO) -> None:
    """Acquire the platform file lock when the optional module is available."""
    try:
        import fcntl

        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    except ImportError:
        pass


def _promote_locked(root: Path, metadata: ApprovalRecord, files: dict[str, bytes]) -> str:
    """Reconcile one approval while its lesson lock is held."""
    lesson_id = metadata.lesson_id
    expected = metadata.source_sha256
    destination = WorkspacePaths(root).lessons_root / lesson_id
    current = _current_source_hash(destination)
    if current == expected:
        _remove_previous_residue(destination, expected)
        return expected
    replaces = metadata.replaces_source_sha256
    approval_digest = expected.removeprefix("sha256:")
    previous = destination.parent / f".{lesson_id}.previous-{approval_digest}"
    interrupted_previous = _current_source_hash(previous)
    _validate_promotion_state(destination, current, interrupted_previous, replaces, lesson_id)
    staging = destination.parent / f".{lesson_id}.next-{approval_digest}"
    if staging.exists():
        shutil.rmtree(staging)
    _write_source_package(staging, files)
    if destination.exists():
        if previous.exists():
            shutil.rmtree(previous)
        destination.rename(previous)
    staging.rename(destination)
    if _current_source_hash(destination) != expected:
        raise LessonPromotionError(f"installed lesson {lesson_id} failed digest verification")
    if previous.exists():
        shutil.rmtree(previous)
    return expected


def _remove_previous_residue(destination: Path, expected: str) -> None:
    """Remove a stale interrupted-promotion residue after an idempotent hit."""
    residue = destination.parent / f".{destination.name}.previous-{expected.removeprefix('sha256:')}"
    if residue.exists():
        shutil.rmtree(residue)


def _validate_promotion_state(
    destination: Path,
    current: str | None,
    interrupted_previous: str | None,
    replaces: str | None,
    lesson_id: str,
) -> None:
    """Ensure canonical source still matches the approval predecessor."""
    if destination.exists() and current is None:
        raise LessonPromotionError(f"canonical lesson {lesson_id} is incomplete: {destination}")
    if current is None:
        if interrupted_previous != replaces and not (replaces is None and not destination.exists()):
            raise LessonPromotionError(f"canonical lesson {lesson_id} changed since approval")
    elif current != replaces:
        raise LessonPromotionError(
            f"canonical lesson {lesson_id} changed since approval (expected {replaces}, found {current})"
        )


def _promote_batch_item(
    root: Path,
    result: LessonResult,
    *,
    replace: bool,
) -> tuple[_PreparedPackage, Path | None, LessonPromotionOutcome]:
    """Prepare and promote one selected parked result."""
    package = _prepare_package(root, result)
    paths = WorkspacePaths(root)
    existing = _current_source_hash(paths.lessons_root / package.catalog_id)
    if existing == package.source_hash:
        matching = paths.lesson_approvals_root / package.catalog_id / package.source_hash.removeprefix("sha256:")
        if not matching.is_dir():
            raise LessonPromotionError(f"{package.catalog_id}: canonical source has no matching approval")
        approval = read_lesson_approval(root, matching)
        return (
            package,
            matching,
            LessonPromotionOutcome(
                catalog_id=package.catalog_id,
                approval_path=str(matching.relative_to(root)),
                approval_id=approval.approval_id,
                status="unchanged",
            ),
        )
    if (paths.lessons_root / package.catalog_id).exists() and not replace:
        raise LessonPromotionError(
            "canonical packages already exist with different content; rerun with --replace: " + package.catalog_id
        )
    from lesson_builder.workflow.lesson_generation.runner import resume_lesson_package

    summary = resume_lesson_package(package.run_id, "accept", repo_root=root)
    if summary.get("stage") != "accepted" or not summary.get("approval_path"):
        raise LessonPromotionError(f"acceptance did not complete promotion ({summary})")
    approval_path = root / str(summary["approval_path"])
    approval = read_lesson_approval(root, approval_path)
    return (
        package,
        approval_path,
        LessonPromotionOutcome(
            catalog_id=package.catalog_id,
            approval_path=str(approval_path.relative_to(root)),
            approval_id=approval.approval_id,
            status="promoted",
        ),
    )


def _write_legacy_approval_receipt(root: Path, batch_id: str, approval_paths: list[Path]) -> None:
    """Write the compatibility receipt for consumers of the old batch index."""
    legacy_approval = build_approval_receipt_path(root, batch_id)
    legacy_approval.parent.mkdir(parents=True, exist_ok=True)
    legacy_approval.write_text(
        yaml.safe_dump(
            {
                "schema_version": "compatibility-index-v1",
                "batch_id": batch_id,
                "approvals": [str(path.relative_to(root)) for path in approval_paths],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _promotion_result(
    batch: LessonBatchResult,
    source_batch_path: Path,
    prepared: list[_PreparedPackage],
    approval_paths: list[Path],
    unchanged_ids: set[str],
    outcomes: list[LessonPromotionOutcome],
    root: Path,
    *,
    status: Literal["promoted", "incomplete", "distribution_failed"] = "promoted",
    distribution_status: Literal["assembled", "failed", "not_attempted"] = "assembled",
) -> LessonPromotionResult:
    """Build the structured durable summary for success or partial progress."""
    paths = WorkspacePaths(root)
    return LessonPromotionResult(
        status=status,
        batch_id=batch.batch_id,
        source_batch_path=str(source_batch_path),
        source_root=str(paths.lessons_root.relative_to(root)),
        distribution_root=str((paths.dist_root / "lessons").relative_to(root)),
        approval_path=str(approval_paths[0].relative_to(root)) if approval_paths else "",
        approval_paths=[str(path.relative_to(root)) for path in approval_paths],
        total=len(outcomes),
        promoted=sum(outcome.status == "promoted" for outcome in outcomes),
        unchanged=sum(outcome.status == "unchanged" for outcome in outcomes),
        catalog_ids=[outcome.catalog_id for outcome in outcomes],
        source_hashes={package.catalog_id: package.source_hash for package in prepared},
        export_hashes={package.catalog_id: package.export_hash for package in prepared},
        outcomes=outcomes,
        distribution_status=distribution_status,
    )


def _current_source_hash(path: Path) -> str | None:
    """Return the canonical digest, or ``None`` when no complete package exists."""
    if not path.is_dir() or any(not (path / name).is_file() for name in K_PROMOTION_AUTHORING_FILES):
        return None
    return _hash_files({name: (path / name).read_bytes() for name in K_PROMOTION_AUTHORING_FILES})


class _PreparedPackage:
    """Validated source and export held in memory before canonical writes."""

    def __init__(
        self,
        catalog_id: str,
        run_id: str,
        source_dir: Path,
        source_files: dict[str, bytes],
        source_hash: str,
        export_text: str,
        stage_statuses: dict[str, str],
    ) -> None:
        self.catalog_id = catalog_id
        self.run_id = run_id
        self.source_dir = source_dir
        self.source_files = source_files
        self.source_hash = source_hash
        self.export_text = export_text
        self.export_hash = _hash_text(export_text)
        self.stage_statuses = stage_statuses


def _load_batch(path: Path) -> LessonBatchResult:
    """Load and validate one batch checkpoint."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LessonPromotionError(f"cannot read batch checkpoint {path}: {exc}") from exc
    try:
        return LessonBatchResult.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - present one boundary error to CLI
        raise LessonPromotionError(f"invalid batch checkpoint {path}: {exc}") from exc


def _select_results(results: list[LessonResult], catalog_ids: list[str] | None) -> list[LessonResult]:
    """Select requested result rows without accepting unknown catalog IDs."""
    by_id = {result.catalog_id: result for result in results}
    if catalog_ids is None:
        return sorted(results, key=lambda result: result.catalog_id)
    requested = {value.strip() for value in catalog_ids if value.strip()}
    unknown = sorted(requested - set(by_id))
    if unknown:
        raise LessonPromotionError(f"catalog IDs are not in the batch: {', '.join(unknown)}")
    return [by_id[catalog_id] for catalog_id in sorted(requested)]


def _validate_results_are_parked(results: list[LessonResult]) -> None:
    """Fail before writing when any selected package is incomplete."""
    invalid = [
        f"{result.catalog_id}={result.status}/{result.human_gate}"
        for result in results
        if result.status != "parked" or result.human_gate != "pending"
    ]
    if invalid:
        raise LessonPromotionError("all selected packages must be parked at the artifact gate: " + ", ".join(invalid))


def _prepare_package(root: Path, result: LessonResult) -> _PreparedPackage:
    """Read, compile, and export one parked source package in a temp directory."""
    if not result.generated_source_dir:
        raise LessonPromotionError(f"{result.catalog_id}: generated source path is missing")
    validate_slug(result.catalog_id)
    source = root / result.generated_source_dir
    if not source.is_dir():
        raise LessonPromotionError(f"{result.catalog_id}: source directory is missing: {source}")
    attestations = read_stage_attestations(source)
    attestation_issues = verify_source_attestations(source)
    if attestation_issues:
        raise LessonPromotionError(
            f"{result.catalog_id}: generated source review evidence is not current: " + "; ".join(attestation_issues)
        )
    try:
        source_files = {name: (source / name).read_bytes() for name in K_PROMOTION_AUTHORING_FILES}
    except OSError as exc:
        raise LessonPromotionError(f"{result.catalog_id}: authored source is incomplete: {exc}") from exc
    source_hash = _hash_files(source_files)
    try:
        export, _plan_metadata = create_lesson_packet_from_source(source)
    except Exception as exc:  # noqa: BLE001 - package-level diagnostic
        raise LessonPromotionError(f"{result.catalog_id}: source failed export validation: {exc}") from exc
    export_text = json.dumps(export, ensure_ascii=False, indent=2) + "\n"
    return _PreparedPackage(
        result.catalog_id,
        result.run_id,
        source,
        source_files,
        source_hash,
        export_text,
        summary_statuses(attestations),
    )


def _validate_current_slot_plans(root: Path, batch: LessonBatchResult, results: list[LessonResult]) -> None:
    """Reject promotion when a selected package belongs to an older slot plan."""
    plan_path = root / batch.plan_path
    if not plan_path.is_file():
        raise LessonPromotionError(f"curriculum plan not found at {plan_path}; promotion requires it")
    plan = load_lesson_plan(plan_path)
    slots = {slot.catalog_id: slot for slot in plan.slots}
    mismatches: list[str] = []
    for result in results:
        slot = slots.get(result.catalog_id)
        if slot is None:
            mismatches.append(f"{result.catalog_id}: no current slot")
            continue
        expected = _hash_text(render_lesson_plan(slot))
        if result.slot_plan_hash != expected:
            mismatches.append(f"{result.catalog_id}: slot plan hash is stale")
    if mismatches:
        raise LessonPromotionError(
            "selected packages are not bound to the current catalog plan: " + "; ".join(mismatches)
        )


def _write_source_package(destination: Path, source_files: dict[str, bytes]) -> None:
    """Write exactly the authored Markdown/YAML files, excluding derived files."""
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in source_files.items():
        _atomic_write_bytes(destination / name, content)


def _hash_files(files: dict[str, bytes]) -> str:
    """Hash authored filenames and bytes in stable order."""
    hasher = hashlib.new(K_PROMOTION_HASH_ALGORITHM)
    for name in K_PROMOTION_AUTHORING_FILES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(files[name])
    return f"{K_PROMOTION_HASH_ALGORITHM}:{hasher.hexdigest()}"


def _hash_text(text: str) -> str:
    """Hash one UTF-8 export."""
    return f"{K_PROMOTION_HASH_ALGORITHM}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes through a sibling temporary file and atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp_name)
        raise


__all__ = [
    "LessonPromotionError",
    "promote_lesson_approval",
    "promote_lesson_batch",
]
