"""Entry points: `read_workbench_record`, `write_workbench_record` and `list_workbench_record_identities` own ignored records."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths


def read_workbench_record[T: BaseModel](
    root: Path,
    collection: Literal[
        "proposals",
        "chat",
        "reviews",
        "curriculum",
        "exercise_alignments",
        "audio_plans",
        "audio_index",
        "audio_spend",
        "audio_release",
    ],
    identity: str,
    schema: type[T],
) -> T:
    """Read a validated record or construct its empty state when absent."""
    path = _record_path(root, collection, identity)
    return schema.model_validate_json(path.read_bytes()) if path.is_file() else schema()


def write_workbench_record(
    root: Path,
    collection: Literal[
        "proposals",
        "chat",
        "reviews",
        "curriculum",
        "exercise_alignments",
        "audio_plans",
        "audio_index",
        "audio_spend",
        "audio_release",
    ],
    identity: str,
    record: BaseModel,
) -> None:
    """Atomically replace an ignored record after rejecting escaping paths."""
    path = _record_path(root, collection, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomically(path, record.model_dump_json(indent=2) + "\n")


def list_workbench_record_identities(
    root: Path,
    collection: Literal[
        "proposals",
        "chat",
        "reviews",
        "curriculum",
        "exercise_alignments",
        "audio_plans",
        "audio_index",
        "audio_spend",
        "audio_release",
    ],
) -> tuple[str, ...]:
    """List valid stored record names for one collection without following symlinks."""
    directory = WorkspacePaths(root).workbench_root / collection
    if directory.is_symlink() or not directory.is_dir():
        return ()
    return tuple(
        sorted(
            path.stem
            for path in directory.glob("*.json")
            if path.is_file()
            and not path.is_symlink()
            and path.stem
            and all(character in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in path.stem)
        )
    )


def _record_path(root: Path, collection: str, identity: str) -> Path:
    if not identity or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in identity):
        raise ValueError("invalid workbench record identity")
    base = WorkspacePaths(root).workbench_root
    path = base / collection / f"{identity}.json"
    if any(parent.is_symlink() for parent in (root / "store", base, path.parent, path)):
        raise ValueError("workbench store must not contain symlinks")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("workbench store escapes workspace")
    return path


__all__ = ["list_workbench_record_identities", "read_workbench_record", "write_workbench_record"]
