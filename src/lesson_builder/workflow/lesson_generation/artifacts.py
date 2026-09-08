"""Entry points: `build_run_output_root`, `build_batch_output_root`, `build_approval_receipt_path`, and `build_checkpoints_path`.

Not a check itself — the disposable scratch artifact layout for the
lesson-generation workflow. Every scratch write location lives here: the
per-run output directory, the per-batch aggregate directory, the legacy batch
approval receipt, and the shared checkpoint database. The directory names keep
their historical values (``store/scratch/catalog_generation`` and
``store/catalog_generation_checkpoints.db``) so existing runs, checkpoints,
and thread identifiers stay resumable; nothing here is a tracked authoring
path.
"""

from __future__ import annotations

from pathlib import Path

from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_CHECKPOINTS_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_SCRATCH_ROOT

K_LESSON_GENERATION_BATCH_DIR_PREFIX = "batch-"
K_LESSON_GENERATION_APPROVALS_DIRNAME = "approvals"
K_LESSON_GENERATION_APPROVAL_RECEIPT_SUFFIX = "-lesson-approval.yaml"
K_LESSON_GENERATION_STORE_DIRNAME = "store"


def build_run_output_root(repo_root: Path, run_id: str) -> Path:
    """Return one run's disposable output directory under the scratch root."""
    return Path(repo_root) / K_LESSON_GENERATION_SCRATCH_ROOT / run_id


def build_batch_output_root(repo_root: Path, batch_id: str) -> Path:
    """Return one batch's disposable aggregate directory under the scratch root."""
    return Path(repo_root) / K_LESSON_GENERATION_SCRATCH_ROOT / f"{K_LESSON_GENERATION_BATCH_DIR_PREFIX}{batch_id}"


def build_approval_receipt_path(repo_root: Path, batch_id: str) -> Path:
    """Return the legacy batch approval receipt path kept for old consumers."""
    return (
        Path(repo_root)
        / K_LESSON_GENERATION_SCRATCH_ROOT
        / K_LESSON_GENERATION_APPROVALS_DIRNAME
        / f"{batch_id}{K_LESSON_GENERATION_APPROVAL_RECEIPT_SUFFIX}"
    )


def build_checkpoints_path(repo_root: Path) -> Path:
    """Return the shared lesson-package checkpoint database for one repository."""
    return Path(repo_root) / K_LESSON_GENERATION_STORE_DIRNAME / K_LESSON_GENERATION_CHECKPOINTS_FILE


__all__ = [
    "K_LESSON_GENERATION_BATCH_DIR_PREFIX",
    "K_LESSON_GENERATION_APPROVALS_DIRNAME",
    "K_LESSON_GENERATION_APPROVAL_RECEIPT_SUFFIX",
    "K_LESSON_GENERATION_STORE_DIRNAME",
    "build_run_output_root",
    "build_batch_output_root",
    "build_approval_receipt_path",
    "build_checkpoints_path",
]
