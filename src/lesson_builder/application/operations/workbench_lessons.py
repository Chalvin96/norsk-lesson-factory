"""Entry points: `list_workbench_lessons` (picker), `resolve_lesson_package` and `build_workbench_preview` (Lesson tab),
`count_workbench_checks` (curriculum check counts)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.author_preview import build_package_preview
from lesson_builder.application.operations.lesson_package_paths import LessonPackagePathError
from lesson_builder.application.operations.lesson_package_paths import resolve_contained_lesson_package
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.distribution.settings import K_REQUIRED_PACKAGE_FILES
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.paths import get_workspace_root

K_WORKBENCH_PREVIEW_CACHE_SIZE = 32
K_WORKBENCH_CHECK_COUNT_CACHE_SIZE = 1024

LessonSource = Literal["draft", "canonical"]


@dataclass(frozen=True)
class WorkbenchLesson:
    """One curriculum lesson as the picker lists it."""

    lesson_id: str
    title: str
    cefr_level: str
    sequence_index: int
    source: Literal["draft", "canonical", "missing"]


@dataclass(frozen=True)
class LessonPackageRef:
    """A contained lesson package and its author-facing source identity."""

    lesson_id: str
    path: Path
    source: LessonSource
    source_label: str


class InvalidLessonPackageError(ValueError):
    """A selected draft or canonical package exists but cannot be read safely."""


def list_workbench_lessons(repo_root: Path, *, plan: CurriculumPlan | None = None) -> tuple[WorkbenchLesson, ...]:
    """List curriculum plan slots in sequence order without compiling any lesson."""
    paths = WorkspacePaths(Path(repo_root))
    plan = plan if plan is not None else read_curriculum(repo_root)
    lessons: list[WorkbenchLesson] = []
    for slot in sorted(plan.slots, key=lambda item: item.sequence_index):
        lesson_id = validate_slug(slot.catalog_id)
        source, _ = _select_source_root(paths, lesson_id)
        lessons.append(
            WorkbenchLesson(
                lesson_id=lesson_id,
                title=slot.title,
                cefr_level=slot.cefr_level,
                sequence_index=slot.sequence_index,
                source=source
                if (paths.drafts_root / lesson_id).is_dir() or (paths.lessons_root / lesson_id).is_dir()
                else "missing",
            )
        )
    return tuple(lessons)


def resolve_lesson_package(repo_root: Path, lesson_id: str, *, canonical_only: bool = False) -> LessonPackageRef:
    """Resolve the selected package; raise ValueError when absent or outside its source root."""
    validate_slug(lesson_id)
    paths = WorkspacePaths(Path(repo_root))
    source, root = _select_source_root(paths, lesson_id, canonical_only=canonical_only)
    source_label = (root.relative_to(paths.root) / lesson_id).as_posix()
    try:
        package = resolve_contained_lesson_package(root, root / lesson_id)
    except LessonPackagePathError as exc:
        if exc.reason == "not_directory":
            raise ValueError(f"lesson package not found: {lesson_id}") from exc
        if exc.reason == "outside_root":
            message = f"lesson package escapes its {source} root: {lesson_id}"
        else:
            message = f"lesson package is missing {exc.filename}: {lesson_id}"
        raise InvalidLessonPackageError(message) from exc
    return LessonPackageRef(
        lesson_id=lesson_id,
        path=package,
        source=source,
        source_label=source_label,
    )


def build_workbench_preview(package: LessonPackageRef) -> AuthorPreview:
    """Build the read-only lesson view for a package returned by `resolve_lesson_package`."""
    signature = _workbench_preview_signature(package.path)
    return deepcopy(_cached_workbench_preview(str(package.path), package.source_label, signature))


def count_workbench_checks(package: LessonPackageRef) -> int:
    """Count a lesson's open deterministic checks without filling the lesson preview cache."""
    signature = _workbench_preview_signature(package.path)
    return _cached_check_count(str(package.path), package.source_label, signature)


def _select_source_root(
    paths: WorkspacePaths,
    lesson_id: str,
    *,
    canonical_only: bool = False,
) -> tuple[LessonSource, Path]:
    """Select canonical source on request; otherwise prefer an existing draft."""
    if canonical_only:
        return "canonical", paths.lessons_root
    draft = paths.drafts_root / lesson_id
    source: LessonSource = "draft" if draft.is_symlink() or draft.is_dir() else "canonical"
    return source, paths.drafts_root if source == "draft" else paths.lessons_root


@lru_cache(maxsize=K_WORKBENCH_PREVIEW_CACHE_SIZE)
def _cached_workbench_preview(
    package_text: str, source_label: str, signature: tuple[tuple[str, int, int, int], ...]
) -> AuthorPreview:
    return build_package_preview(Path(package_text), source_label=source_label)


@lru_cache(maxsize=K_WORKBENCH_CHECK_COUNT_CACHE_SIZE)
def _cached_check_count(package_text: str, source_label: str, signature: tuple[tuple[str, int, int, int], ...]) -> int:
    # Source findings plus compile errors: the lesson page's open check total.
    view = build_package_preview(Path(package_text), source_label=source_label)
    return len(view.audit.get("findings", [])) + len(view.errors)


def _workbench_preview_signature(package: Path) -> tuple[tuple[str, int, int, int], ...]:
    candidates = {package / name for name in K_REQUIRED_PACKAGE_FILES}
    for root in (package, *package.parents, get_workspace_root()):
        paths = WorkspacePaths(root)
        candidates.update((paths.character_registry, paths.terminology_registry))
    signature = []
    for path in candidates:
        try:
            metadata = path.stat()
        except OSError:
            continue
        if path.is_file():
            signature.append((str(path), metadata.st_mtime_ns, metadata.st_size, metadata.st_ino))
    return tuple(sorted(signature))


__all__ = [
    # Errors.
    "InvalidLessonPackageError",
    # Data shapes.
    "LessonPackageRef",
    "LessonSource",
    "WorkbenchLesson",
    # Use cases.
    "build_workbench_preview",
    "count_workbench_checks",
    "list_workbench_lessons",
    "resolve_lesson_package",
]
