"""Entry point: `promote_workbench_lesson`, `pull_canonical_proposal` and `discard_workbench_draft` manage complete packages."""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import TypedDict
from typing import cast
from uuid import uuid4

import yaml

from lesson_builder.application.operations.compile_lesson_package import digest_authored_source
from lesson_builder.application.operations.compile_lesson_package import load_authored_source_snapshot
from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.workbench_audio import project_workbench_audio
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_lesson_gate import CanonicalChangedBlocker
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateSource
from lesson_builder.application.operations.workbench_lesson_gate import check_promotion
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_proposals import check_preview
from lesson_builder.application.operations.workbench_proposals import check_proposal
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_proposals import read_open_proposal
from lesson_builder.application.operations.workbench_proposals import store_proposal
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalBlock
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import read_draft_git_state
from lesson_builder.workspace.workbench_git import require_workbench_commit
from lesson_builder.workspace.workbench_transaction import contained_workbench_path
from lesson_builder.workspace.workbench_transaction import workbench_transaction
from lesson_builder.workspace.workbench_transaction import workbench_tree_files


class WorkbenchPromotionResult(TypedDict):
    """Commit identity and distribution paths returned by promotion."""

    commit: str
    dist_files: list[str]


class CanonicalChanged(ValueError):
    """Conflict transport includes a reviewable diff without silently merging source."""

    def __init__(self, diff: str) -> None:
        super().__init__("canonical_changed")
        self.diff = diff


def promote_workbench_lesson(root: Path, lesson_id: str) -> WorkbenchPromotionResult:
    """Replace one complete canonical package, regenerate provider-free dist and commit with hooks."""
    source = read_workbench_source(root, lesson_id)
    package = resolve_lesson_package(root, lesson_id)
    paths = WorkspacePaths(root)
    canonical = contained_workbench_path(root, paths.lessons_root / lesson_id)
    check = check_preview(build_workbench_preview(package))
    audio = project_workbench_audio(root, lesson_id)
    gate = check_promotion(
        LessonGateSource(
            workbench_source=source,
            proposal=read_open_proposal(root, lesson_id, source),
            blocking_errors=check.status == "failed",
            audio_blocked=bool(audio["blockers"]),
            audio_missing=cast(int, audio["missing_count"]),
            audio_stale=cast(int, audio["stale_count"]),
            canonical_changed=source.exists and canonical_source_digest(root, lesson_id) != source.started_from,
        )
    )
    if not gate.allowed:
        if not source.exists:
            raise ValueError("Promotion requires a draft")
        if any(isinstance(blocker, CanonicalChangedBlocker) for blocker in gate.blockers):
            raise CanonicalChanged(canonical_diff(root, lesson_id))
        raise ValueError("Promotion blocked by current lesson gate")
    dist_before = workbench_tree_files(root, paths.dist_root)
    if read_draft_git_state(root, (paths.dist_root.relative_to(root).as_posix(),)).dirty:
        raise ValueError("Promotion refused: distribution has uncommitted changes")
    draft_files = workbench_tree_files(root, package.path)
    canonical_before = workbench_tree_files(root, canonical)
    allowed = (*draft_files, *canonical_before, *dist_before)
    head = require_workbench_commit(root, allowed)
    old_dist = {name: (root / name).read_bytes() for name in dist_before}
    with workbench_transaction(root, (package.path, canonical, paths.dist_root)):
        if canonical.exists():
            shutil.rmtree(canonical)
        shutil.copytree(package.path, canonical, ignore=shutil.ignore_patterns("draft.yaml"))
        shutil.rmtree(package.path)
        export_distribution(root, exclude_draft_only=True)
        new_dist = workbench_tree_files(root, paths.dist_root)
        changed = sorted(
            name
            for name in set(dist_before) | set(new_dist)
            if old_dist.get(name) != ((root / name).read_bytes() if (root / name).is_file() else None)
        )
        commit_files = tuple(
            sorted(set(draft_files) | set(canonical_before) | set(workbench_tree_files(root, canonical)) | set(changed))
        )
        commit = commit_draft(
            root, lesson_id, commit_files, hooks=True, expected_head=head, message=f"workbench: promote {lesson_id}"
        )
    return {"commit": commit, "dist_files": changed}


def canonical_source_digest(root: Path, lesson_id: str) -> str | None:
    """Use the same exact package digest captured when a draft was seeded."""
    canonical = contained_workbench_path(root, WorkspacePaths(root).lessons_root / lesson_id)
    return digest_authored_source(load_authored_source_snapshot(canonical)) if canonical.is_dir() else None


def canonical_diff(root: Path, lesson_id: str) -> str:
    """Show current draft versus canonical for the conflict dialog."""
    source = read_workbench_source(root, lesson_id)
    canonical = contained_workbench_path(root, WorkspacePaths(root).lessons_root / lesson_id)
    return (
        "\n".join(
            "".join(
                difflib.unified_diff(
                    text.splitlines(True),
                    ((canonical / name).read_text() if (canonical / name).is_file() else "").splitlines(True),
                    fromfile=f"draft/{name}",
                    tofile=f"canonical/{name}",
                )
            )
            for name, text in source.files.items()
        )
        or "Canonical package was removed or replaced."
    )


def pull_canonical_proposal(root: Path, lesson_id: str) -> Proposal | None:
    """Offer the current complete canonical files as checked WP2 proposal blocks."""
    source = read_workbench_source(root, lesson_id)
    if not source.exists:
        raise ValueError("Pull requires a draft")
    canonical = contained_workbench_path(root, WorkspacePaths(root).lessons_root / lesson_id)
    if not canonical.is_dir():
        raise ValueError("Canonical lesson no longer exists; discard the draft or restore canonical source")
    blocks = [
        ProposalBlock(key=name, before=before, after=(canonical / name).read_text(), note="Pull canonical changes")
        for name, before in source.files.items()
        if before != (canonical / name).read_text()
    ]
    if not blocks:
        metadata_path = contained_workbench_path(root, resolve_lesson_package(root, lesson_id).path / "draft.yaml")
        metadata = yaml.safe_load(metadata_path.read_bytes())
        if not isinstance(metadata, dict) or metadata.get("lesson_id") != lesson_id:
            raise ValueError("Draft provenance is invalid")
        canonical_digest = canonical_source_digest(root, lesson_id)
        if canonical_digest is None:
            raise ValueError("Canonical lesson no longer exists; discard the draft or restore canonical source")
        metadata["started_from_sha256"] = canonical_digest
        with workbench_transaction(root, (metadata_path,)):
            write_text_atomically(metadata_path, yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
        return None
    proposal = Proposal(
        proposal_id=uuid4().hex,
        lesson_id=lesson_id,
        source="canonical",
        created_against_sha256=digest_workbench_source(source),
        note="Review canonical changes before accepting.",
        blocks=blocks,
        canonical_sha256=canonical_source_digest(root, lesson_id),
    )
    proposal.check = check_proposal(root, lesson_id, source, proposal)
    store_proposal(root, lesson_id, proposal)
    return proposal


def discard_workbench_draft(root: Path, lesson_id: str, *, confirmed: bool) -> str:
    """Delete a confirmed draft with a single hook-enabled commit, preserving canonical source."""
    if not confirmed:
        raise ValueError("Discard requires confirmation")
    package = resolve_lesson_package(root, lesson_id)
    if package.source != "draft":
        raise ValueError("There is no draft to discard")
    files = workbench_tree_files(root, package.path)
    head = require_workbench_commit(root, files)
    with workbench_transaction(root, (package.path,)):
        shutil.rmtree(package.path)
        return commit_draft(
            root,
            lesson_id,
            files,
            hooks=True,
            expected_head=head,
            allow_empty=True,
            message=f"workbench: discard {lesson_id}",
        )
