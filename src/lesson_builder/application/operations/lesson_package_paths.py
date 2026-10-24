"""Entry point: `resolve_contained_lesson_package` validates one authored package path."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from lesson_builder.domain.distribution.settings import K_REQUIRED_PACKAGE_FILES


def resolve_contained_lesson_package(root: Path, package: Path) -> Path:
    """Resolve a package and its required files under ``root``, or raise a structured error."""
    resolved_root = root.resolve()
    resolved_package = package.resolve()
    if resolved_package != resolved_root and resolved_root not in resolved_package.parents:
        raise LessonPackagePathError("outside_root")
    if not resolved_package.is_dir():
        raise LessonPackagePathError("not_directory")
    for filename in K_REQUIRED_PACKAGE_FILES:
        source_path = (resolved_package / filename).resolve()
        if resolved_root not in source_path.parents or not source_path.is_file():
            raise LessonPackagePathError("missing_file", filename=filename)
    return resolved_package


class LessonPackagePathError(ValueError):
    """A package path is absent, incomplete, or outside its allowed source root."""

    def __init__(
        self,
        reason: Literal["outside_root", "not_directory", "missing_file"],
        *,
        filename: str | None = None,
    ) -> None:
        self.reason = reason
        self.filename = filename
        super().__init__(reason)


__all__ = ["LessonPackagePathError", "resolve_contained_lesson_package"]
