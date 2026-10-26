"""Entry point: `read_workbench_source`, `write_workbench_file`, `write_workbench_block`, `save_workbench_draft` for HTTP editing."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.compile_lesson_package import digest_authored_source
from lesson_builder.application.operations.compile_lesson_package import load_authored_source_snapshot
from lesson_builder.application.operations.workbench_lessons import LessonPackageRef
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_FILES
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.formats.workbench_source import emit_block_fields
from lesson_builder.formats.workbench_source import normalize_source
from lesson_builder.formats.workbench_source import replace_source_span
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import read_draft_git_state


@dataclass(frozen=True)
class WorkbenchSource:
    """Source files, source spans and draft status captured for the editor."""

    files: dict[str, str]
    blocks: tuple[SourceBlock, ...]
    exists: bool
    started_from: str | None
    dirty: bool
    last_commit: str | None
    created_at: str | None = None


class WorkbenchSourceNotFound(ValueError):
    """A requested source file or block does not exist."""


def read_workbench_source(repo_root: Path, lesson_id: str, *, canonical_only: bool = False) -> WorkbenchSource:
    """Capture source with exact newlines, optionally bypassing a workbench draft."""
    package = resolve_lesson_package(repo_root, lesson_id, canonical_only=canonical_only)
    files = {
        name: _contained_file(package, name).read_bytes().decode("utf-8") for name in K_APPLICATION_WORKBENCH_FILES
    }
    blocks = tuple(
        block for block in split_lesson_source(files["lesson.md"]).blocks if block.kind != "exercise"
    ) + split_exercise_source(files["exercises.yaml"])
    exists = package.source == "draft"
    metadata = _contained_file(package, "draft.yaml")
    started_from = None
    created_at = None
    if exists and metadata.is_file():
        raw = yaml.safe_load(metadata.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            started_from = raw.get("started_from_sha256")
            created_at = raw.get("created_at")
    status = read_draft_git_state(repo_root, _draft_paths(repo_root, package)) if exists else None
    return WorkbenchSource(
        files,
        blocks,
        exists,
        started_from,
        status.dirty if status else False,
        status.last_commit if status else None,
        created_at,
    )


def write_workbench_file(repo_root: Path, lesson_id: str, name: str, text: str) -> AuthorPreview:
    """Seed when needed, write only an allowed draft file, then compile and audit it."""
    if name not in K_APPLICATION_WORKBENCH_FILES:
        raise WorkbenchSourceNotFound("source file not found")
    package = _ensure_draft(repo_root, lesson_id)
    write_text_atomically(_contained_file(package, name), normalize_source(text))
    return build_workbench_preview(package)


def write_workbench_block(
    repo_root: Path,
    lesson_id: str,
    key: str,
    *,
    source: str | None = None,
    fields: dict[str, str] | None = None,
) -> AuthorPreview:
    """Replace one Markdown block or YAML item and check the resulting draft."""
    captured = read_workbench_source(repo_root, lesson_id)
    block = next((item for item in captured.blocks if item.key == key), None)
    if block is None:
        raise WorkbenchSourceNotFound("source block not found")
    if (source is None) == (fields is None):
        raise ValueError("provide source or fields")
    replacement = source if source is not None else emit_block_fields(block, fields or {})
    name = "exercises.yaml" if block.kind == "exercise" else "lesson.md"
    text = replace_source_span(captured.files[name], block, replacement)
    package = _ensure_draft(repo_root, lesson_id)
    write_text_atomically(_contained_file(package, name), text)
    return build_workbench_preview(package)


def save_workbench_draft(repo_root: Path, lesson_id: str) -> tuple[str, AuthorPreview]:
    """Check and commit a draft in the served repository without publishing."""
    package = resolve_lesson_package(repo_root, lesson_id)
    if package.source != "draft":
        raise ValueError("There are no draft changes to commit")
    _require_draft_path(repo_root, package)
    check = build_workbench_preview(package)
    commit = commit_draft(repo_root, lesson_id, _draft_paths(repo_root, package))
    return commit, check


def _ensure_draft(repo_root: Path, lesson_id: str) -> LessonPackageRef:
    package = resolve_lesson_package(repo_root, lesson_id)
    if package.source == "draft":
        _require_draft_path(repo_root, package)
        return package
    paths = WorkspacePaths(repo_root)
    if paths.drafts_root.is_symlink():
        raise ValueError("draft root must not be a symlink")
    draft = paths.drafts_root / lesson_id
    if not draft.resolve().is_relative_to(repo_root.resolve()):
        raise ValueError("draft path escapes workspace")
    for item in package.path.rglob("*"):
        if item.is_symlink():
            raise ValueError("canonical package contains symlinks")
    digest = digest_authored_source(load_authored_source_snapshot(package.path))
    paths.drafts_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".workbench-", dir=paths.drafts_root) as temporary:
        seeded = Path(temporary) / lesson_id
        shutil.copytree(package.path, seeded)
        (seeded / "draft.yaml").write_text(
            yaml.safe_dump(
                {"lesson_id": lesson_id, "started_from_sha256": digest, "created_at": datetime.now(UTC).isoformat()},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        seeded.rename(draft)
    return resolve_lesson_package(repo_root, lesson_id)


def _require_draft_path(root: Path, package: LessonPackageRef) -> None:
    expected = WorkspacePaths(root).drafts_root / package.lesson_id
    if expected.is_symlink() or expected.parent.is_symlink() or package.path != expected.absolute():
        raise ValueError("draft path escapes lesson package")


def _contained_file(package: LessonPackageRef, name: str) -> Path:
    target = package.path / name
    if target.is_symlink() or not target.resolve().is_relative_to(package.path.resolve()):
        raise ValueError("source path escapes lesson package")
    return target


def _draft_paths(root: Path, package: LessonPackageRef) -> tuple[str, ...]:
    paths = []
    for item in sorted(package.path.rglob("*")):
        if item.is_symlink() or not item.resolve().is_relative_to(package.path):
            raise ValueError("draft path escapes lesson package")
        if item.is_file():
            paths.append(item.relative_to(root).as_posix())
    return tuple(paths)
