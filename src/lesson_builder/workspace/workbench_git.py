"""Entry points: `read_draft_git_state` supports inspection, and `require_workbench_commit` and `commit_draft` support Saves."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from lesson_builder.workspace.settings import K_WORKSPACE_GIT_TIMEOUT_SECONDS
from lesson_builder.workspace.settings import K_WORKSPACE_PROTECTED_BRANCHES


@dataclass(frozen=True)
class DraftGitState:
    """Repository status of explicit draft paths."""

    dirty: bool
    last_commit: str | None


class WorkbenchGitError(ValueError):
    """A repository cannot safely perform the requested Save."""


def read_draft_git_state(root: Path, paths: tuple[str, ...]) -> DraftGitState:
    """Read draft status; source inspection also works outside a git repository."""
    try:
        dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all", "--", *paths))
        last = _git(root, "log", "-1", "--format=%H", "--", *paths).strip() or None
    except WorkbenchGitError:
        return DraftGitState(dirty=True, last_commit=None)
    return DraftGitState(dirty=dirty, last_commit=last)


def commit_draft(
    root: Path,
    lesson_id: str,
    paths: tuple[str, ...],
    *,
    message: str | None = None,
    hooks: bool = False,
    expected_head: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Commit only explicit draft paths, refusing protected branches and unrelated staged changes."""
    head = require_workbench_commit(root, paths)
    if expected_head is not None and head != expected_head:
        raise WorkbenchGitError("Commit refused: HEAD moved")
    if _git(root, "rev-parse", "HEAD").strip() != head:
        raise WorkbenchGitError("Save refused: HEAD moved")
    index_before = _git(root, "ls-files", "--stage", "-z", "--", *paths)
    try:
        tracked = set(_git(root, "ls-files", "-z", "--", *paths).split("\0"))
        eligible = tuple(path for path in paths if path in tracked or (root / path).exists())
        if eligible:
            _git(root, "add", "--", *eligible)
        if not allow_empty and not _git(root, "diff", "--cached", "--name-only", "--", *paths).strip():
            raise WorkbenchGitError("There are no draft changes to commit")
        if _git(root, "rev-parse", "HEAD").strip() != head:
            raise WorkbenchGitError("Save refused: HEAD moved")
        options = ("commit",) if hooks else ("-c", "core.hooksPath=/dev/null", "commit", "--no-verify")
        _git(
            root,
            *options,
            "-m",
            message or f"workbench: save {lesson_id}",
            "--only",
            *(("--allow-empty",) if allow_empty else ()),
            *(("--", *eligible) if eligible else ()),
        )
    except WorkbenchGitError:
        removal = "".join(f"0 {'0' * 40}\t{path}\0" for path in paths)
        _git(root, "update-index", "-z", "--index-info", input_text=removal + index_before)
        raise
    return _git(root, "rev-parse", "HEAD").strip()


def require_workbench_commit(root: Path, paths: tuple[str, ...]) -> str:
    """Preflight explicit Save paths before starting provider-backed work; return the captured HEAD."""
    if Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve() != root.resolve():
        raise WorkbenchGitError("Save requires the served workspace to be the repository root")
    try:
        branch = _git(root, "symbolic-ref", "--short", "HEAD").strip()
    except WorkbenchGitError as exc:
        if "not a symbolic ref" not in str(exc):
            raise
        raise WorkbenchGitError(
            "Saving drafts needs a checked-out branch; this workspace is on a detached HEAD."
        ) from exc
    if branch in K_WORKSPACE_PROTECTED_BRANCHES:
        raise WorkbenchGitError("Save requires a working branch; main and master are protected")
    head = _git(root, "rev-parse", "HEAD").strip()
    staged = set(filter(None, _git(root, "diff", "--cached", "--name-only", "-z").split("\0")))
    if staged - set(paths):
        raise WorkbenchGitError("Save refused: unrelated paths are already staged")
    return head


def _git(root: Path, *arguments: str, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
            timeout=K_WORKSPACE_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        message = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise WorkbenchGitError(message or "git operation failed") from exc
    return result.stdout
