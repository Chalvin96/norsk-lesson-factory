"""Entry point: `workbench_transaction` restores explicit file trees after a failed mutation."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def contained_workbench_path(root: Path, path: Path) -> Path:
    """Reject symlinks and escaping paths before reading, copying or deleting authoring data."""
    if not path.absolute().is_relative_to(root.absolute()) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("workbench path escapes workspace")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("workbench path contains a symlink")
    if path.is_dir() and any(item.is_symlink() for item in path.rglob("*")):
        raise ValueError("workbench package contains symlinks")
    return path


@contextmanager
def workbench_transaction(root: Path, targets: tuple[Path, ...]) -> Iterator[None]:
    """Roll back only owned paths; the commit operation separately preserves their staged entries."""
    with TemporaryDirectory(prefix="workbench-rollback-") as temporary:
        saved = Path(temporary)
        for index, target in enumerate(targets):
            contained_workbench_path(root, target)
            _copy(target, saved / str(index))
        try:
            yield
        except Exception:
            for index, target in enumerate(targets):
                _remove(target)
                _copy(saved / str(index), target)
            raise


def workbench_tree_files(root: Path, target: Path) -> tuple[str, ...]:
    """List contained explicit paths for safe commits, including paths captured before deletion."""
    contained_workbench_path(root, target)
    return tuple(item.relative_to(root).as_posix() for item in sorted(target.rglob("*")) if item.is_file())


def _copy(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _remove(target: Path) -> None:
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
