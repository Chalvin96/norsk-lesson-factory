"""Entry points: strict and collecting lesson-package compilation.

These operations are the canonical application boundary for one authored lesson
package. They capture the source files, run the provider-free source audit,
construct the typed lesson, and run deterministic lesson gates. Strict callers
raise on any blocking finding; collecting callers retain findings and readable
source for inspection tools.

`compile_lesson_package` serves filesystem-backed strict callers,
`collect_lesson_package` serves author inspection, and
`compile_lesson_source_snapshot` serves `prepare_source_package` after capture.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.audit_source import audit_source_text
from lesson_builder.application.operations.load_exercises import ParsedExerciseSource
from lesson_builder.application.operations.load_exercises import parse_exercise_source
from lesson_builder.application.operations.load_lesson import load_lesson_source
from lesson_builder.application.operations.load_terminology import load_terminology_bans
from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.models.source_audit import MechanicalFinding
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.checks.gate_manager import gate_lesson_results
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.workspace.paths import WorkspacePaths

K_COMPILE_LESSON_PLAN_FILE = "plan.md"
K_COMPILE_LESSON_FILE = "lesson.md"
K_COMPILE_EXERCISES_FILE = "exercises.yaml"
K_COMPILE_HASH_ALGORITHM = "sha256"
K_COMPILE_DIGEST_PREFIX = "sha256:"


@dataclass(frozen=True)
class AuthoredSourceSnapshot:
    """Exact authored package text captured for one compilation."""

    lesson_text: str
    exercises_text: str
    plan_text: str


@dataclass(frozen=True)
class LessonPackageCompilation:
    """Collected audit, lesson, plan, and deterministic gate results."""

    audit: MechanicalAudit
    lesson: Lesson | None
    plan: dict[str, Any]
    quality_results: tuple[CheckResult, ...]
    errors: tuple[str, ...]
    source_digest: str

    @property
    def is_valid(self) -> bool:
        """Return whether the collected package is safe for strict callers."""
        return (
            self.lesson is not None
            and not self.errors
            and not self.audit.material_findings
            and not any(result.is_blocking for result in self.quality_results)
        )


def compile_lesson_package(
    source_dir: Path,
    *,
    terminology_bans: TerminologyBans | None = None,
) -> LessonPackageCompilation:
    """Strictly compile one authored package from one captured source snapshot."""
    source = load_authored_source_snapshot(Path(source_dir))
    result = _collect_source_snapshot(
        source,
        terminology_bans=terminology_bans,
        source_dir=Path(source_dir),
    )
    _require_compilation_pass(result)
    return result


def collect_lesson_package(
    source_dir: Path,
    *,
    terminology_bans: TerminologyBans | None = None,
) -> LessonPackageCompilation:
    """Collect source findings for a tolerant authoring inspection caller."""
    root = Path(source_dir)
    source, read_errors = _read_authored_source_collecting(root)
    result = _collect_source_snapshot(
        source,
        terminology_bans=terminology_bans,
        source_dir=root,
    )
    if read_errors:
        return LessonPackageCompilation(
            audit=result.audit,
            lesson=result.lesson,
            plan=result.plan,
            quality_results=result.quality_results,
            errors=(*read_errors, *result.errors),
            source_digest=result.source_digest,
        )
    return result


def compile_lesson_source_snapshot(
    source: AuthoredSourceSnapshot,
    *,
    source_dir: Path,
    terminology_bans: TerminologyBans | None = None,
) -> LessonPackageCompilation:
    """Strictly compile an already captured source snapshot.

    ``prepare_source_package`` uses this entry point after capturing source
    bytes, so generation shares the canonical compiler without rereading them.
    """
    result = _collect_source_snapshot(
        source,
        terminology_bans=terminology_bans,
        source_dir=Path(source_dir),
    )
    _require_compilation_pass(result)
    return result


def load_authored_source_snapshot(root: Path) -> AuthoredSourceSnapshot:
    """Read all authored package files once for a strict compilation."""
    return AuthoredSourceSnapshot(
        lesson_text=_read_authored_text(root / K_COMPILE_LESSON_FILE),
        exercises_text=_read_authored_text(root / K_COMPILE_EXERCISES_FILE),
        plan_text=_read_authored_text(root / K_COMPILE_LESSON_PLAN_FILE),
    )


def digest_authored_source(source: AuthoredSourceSnapshot) -> str:
    """Return a deterministic digest for the captured authored package text."""
    digest = hashlib.new(K_COMPILE_HASH_ALGORITHM)
    for filename, text in (
        (K_COMPILE_LESSON_PLAN_FILE, source.plan_text),
        (K_COMPILE_LESSON_FILE, source.lesson_text),
        (K_COMPILE_EXERCISES_FILE, source.exercises_text),
    ):
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return K_COMPILE_DIGEST_PREFIX + digest.hexdigest()


def _collect_source_snapshot(
    source: AuthoredSourceSnapshot,
    *,
    terminology_bans: TerminologyBans | None,
    source_dir: Path,
) -> LessonPackageCompilation:
    """Collect all deterministic findings from one immutable source snapshot."""
    errors: list[str] = []
    parsed_exercises = _parse_exercises(source, errors)
    audit = _audit_source(source, parsed_exercises, errors)
    plan = _parse_plan(source.plan_text, errors)
    lesson = _build_lesson(
        source,
        audit=audit,
        errors=errors,
        parsed_exercises=parsed_exercises,
    )
    quality_results: tuple[CheckResult, ...] = ()
    if lesson is not None:
        try:
            bans = terminology_bans if terminology_bans is not None else _default_terminology_bans(source_dir)
            quality_results = tuple(
                gate_lesson_results(
                    lesson.model_dump(mode="json"),
                    terminology_bans=bans,
                    content_kind=plan.get("kind") if isinstance(plan.get("kind"), str) else None,
                    exercise_handles=(dict(parsed_exercises.handle_by_id) if parsed_exercises is not None else None),
                )
            )
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"deterministic lesson gates could not complete: {exc}")
    return LessonPackageCompilation(
        audit=audit,
        lesson=lesson,
        plan=plan,
        quality_results=quality_results,
        errors=tuple(errors),
        source_digest=digest_authored_source(source),
    )


def _audit_source(
    source: AuthoredSourceSnapshot,
    parsed_exercises: ParsedExerciseSource | None,
    errors: list[str],
) -> MechanicalAudit:
    """Audit the captured lesson and exercise text, retaining collector errors."""
    try:
        return audit_source_text(
            source.exercises_text,
            lesson_text=source.lesson_text,
            parsed_exercises=parsed_exercises,
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"source audit could not complete: {exc}")
        return MechanicalAudit(
            status="invalid",
            findings=[
                MechanicalFinding(
                    code="source-audit-invalid",
                    severity="blocking",
                    artifact="lesson.md",
                    location="document",
                    evidence=str(exc),
                    explanation="The provider-free source audit could not validate this package.",
                )
            ],
        )


def _build_lesson(
    source: AuthoredSourceSnapshot,
    *,
    audit: MechanicalAudit,
    errors: list[str],
    parsed_exercises: ParsedExerciseSource | None,
) -> Lesson | None:
    """Load the audited lesson, retrying without audit only for tolerant display."""
    try:
        lesson = load_lesson_source(
            source.lesson_text,
            source.exercises_text,
            audit=audit,
            parsed_exercises=parsed_exercises,
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        if not audit.material_findings:
            errors.append(f"lesson compilation could not complete: {exc}")
            return None
        try:
            lesson = load_lesson_source(
                source.lesson_text,
                source.exercises_text,
                audit=None,
                parsed_exercises=parsed_exercises,
            )
        except (OSError, TypeError, ValueError, yaml.YAMLError) as fallback_exc:
            errors.append(f"lesson compilation could not complete: {fallback_exc}")
            return None
    if not any(element.element_kind == "exercise" for element in lesson.elements):
        errors.append("lesson package must contain at least one exercise")
        return None
    return lesson


def _parse_exercises(source: AuthoredSourceSnapshot, errors: list[str]) -> ParsedExerciseSource | None:
    """Parse the captured exercise document once for audit and construction."""
    try:
        return parse_exercise_source(source.exercises_text)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"exercise source could not be parsed: {exc}")
        return None


def _parse_plan(plan_text: str, errors: list[str]) -> dict[str, Any]:
    """Parse plan metadata from the captured plan text."""
    try:
        metadata, _ = parse_source_frontmatter(plan_text)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"lesson plan could not be loaded: {exc}")
        return {}
    return metadata


def _default_terminology_bans(source_dir: Path) -> TerminologyBans:
    """Load the nearest workspace terminology policy, preserving fixture support."""
    for candidate in (source_dir, *source_dir.parents):
        registry_path = WorkspacePaths(candidate).terminology_registry
        if registry_path.is_file():
            return load_terminology_bans(registry_path, use_cache=False)
    try:
        return load_terminology_bans(use_cache=False)
    except FileNotFoundError:
        return TerminologyBans()


def _require_compilation_pass(result: LessonPackageCompilation) -> None:
    """Raise one actionable error when strict compilation finds a defect."""
    if result.audit.material_findings:
        details = "; ".join(
            f"{finding.code}@{finding.location} ({finding.severity}): {finding.explanation}"
            for finding in result.audit.material_findings
        )
        raise ValueError(f"mechanical exercise preflight blocked lesson loading: {details}")
    if result.errors:
        raise ValueError("lesson-package compilation failed: " + "; ".join(result.errors))
    blocking_results = [result_item for result_item in result.quality_results if result_item.is_blocking]
    if blocking_results:
        details = "; ".join(
            f"{result_item.check_id}({result_item.unit_id}): {result_item.message}" for result_item in blocking_results
        )
        raise ValueError(f"deterministic gate blocked lesson-package compilation: {details}")


def _read_authored_source_collecting(root: Path) -> tuple[AuthoredSourceSnapshot, tuple[str, ...]]:
    """Read package files while preserving a useful collector result."""
    errors: list[str] = []

    def read(filename: str) -> str:
        try:
            return _read_authored_text(root / filename)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{filename} could not be read: {exc}")
            return ""

    return (
        AuthoredSourceSnapshot(
            lesson_text=read(K_COMPILE_LESSON_FILE),
            exercises_text=read(K_COMPILE_EXERCISES_FILE),
            plan_text=read(K_COMPILE_LESSON_PLAN_FILE),
        ),
        tuple(errors),
    )


def _read_authored_text(path: Path) -> str:
    """Read UTF-8 source without normalizing line endings used by its digest."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


__all__ = [
    "AuthoredSourceSnapshot",
    "LessonPackageCompilation",
    "collect_lesson_package",
    "compile_lesson_package",
    "compile_lesson_source_snapshot",
    "digest_authored_source",
    "load_authored_source_snapshot",
]
