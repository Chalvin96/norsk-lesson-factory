"""Entry points: `read_workbench_settings_text`, `write_workbench_settings_text`, and `reset_workbench_settings` manage the local override file."""

from __future__ import annotations

from pathlib import Path

from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths


def read_workbench_settings_text(root: Path) -> str | None:
    """Read the ignored settings override when it exists."""
    path = _settings_path(root)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_workbench_settings_text(root: Path, content: str) -> None:
    """Atomically replace the local settings override after checking containment."""
    path = _settings_path(root)
    write_text_atomically(path, content, create_parent=True)


def reset_workbench_settings(root: Path) -> None:
    """Remove the local override so the current config.yaml values become active again."""
    _settings_path(root).unlink(missing_ok=True)


def _settings_path(root: Path) -> Path:
    workspace = Path(root).resolve()
    path = WorkspacePaths(workspace).workbench_settings
    if not path.resolve(strict=False).is_relative_to(workspace):
        raise ValueError("workbench settings path escapes workspace")
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("workbench settings path must not contain symlinks")
    return path


__all__ = ["read_workbench_settings_text", "reset_workbench_settings", "write_workbench_settings_text"]
