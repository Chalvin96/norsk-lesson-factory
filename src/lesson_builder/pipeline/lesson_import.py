"""Entry point: ``import_lesson`` and ``regenerate_dist``.

Importer surface for Journey 2 ("import + improve"): land a genuinely EXTERNAL
lesson under ``data/lessons/<slug>.json`` plus an acceptance-ledger entry so the
existing ``improve`` / ``graph run`` QA back-half can take it to accepted
artifacts. Recovery is git over ``data/lessons/``; ``dist/`` is a derived
serving projection regenerated from data, never re-imported.

Two modes, one importable core:

* ``import_lesson(file, ...)`` -- import ONE externally-authored internal
  ``Lesson`` file. Defaults to status ``imported_unverified`` so an external
  lesson does NOT silently become a trusted regression baseline; the human gate
  (and a later ``graph run`` accept) is what promotes it.
* ``regenerate_dist(root)`` -- derive ``dist/lessons/*.json`` FROM
  ``data/lessons/*.json`` via ``lesson_to_export``. Idempotent; unchanged
  exports are skipped.

Scratch-vs-commit mirrors ``graph_runner``: ``import_lesson`` writes under
``output_root`` (defaulting to ``repo_root``) and an ``acceptance_log_path``
the caller selects, so an ad-hoc import can target ``store/scratch/`` instead of
the tracked tree.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from lesson_builder.pipeline.lesson_acceptance_log import (
    LessonAcceptanceEntry,
    LessonAcceptanceStatus,
    SourceKind,
    append_entry,
    latest_for_slug,
)
from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.schema import Lesson

K_IMPORT_DEFAULT_STATUS: LessonAcceptanceStatus = "imported_unverified"
K_IMPORT_SOURCE_KIND: SourceKind = "external_import"


class LessonImportError(Exception):
    """An import input could not be read or validated; nothing was written."""


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _acceptance_log_path(root: Path) -> Path:
    return root / "data" / "lesson_acceptance_log.jsonl"


def _write_internal_lesson(*, lessons_dir: Path, slug: str, internal_text: str) -> bool:
    """Write ``data/lessons/<slug>.json`` and return whether it replaced a file."""
    internal_dest = lessons_dir / f"{slug}.json"
    is_update = internal_dest.exists()
    internal_dest.write_text(internal_text, encoding="utf-8")
    return is_update


def _append_import_entry(
    *,
    acceptance_log: Path,
    slug: str,
    run_id: str,
    internal_text: str,
    export_text: str,
    status: LessonAcceptanceStatus,
) -> None:
    append_entry(
        acceptance_log,
        LessonAcceptanceEntry(
            slug=slug,
            ts=_now_iso(),
            run_id=run_id,
            source_kind=K_IMPORT_SOURCE_KIND,
            source_lesson_hash=_sha(internal_text),
            requirements_hash=None,
            reviewer="system",
            status=status,
            export_path=f"dist/lessons/{slug}.json",
            export_hash=_sha(export_text),
        ),
    )


def _import_one(
    *,
    lessons_dir: Path,
    acceptance_log: Path,
    run_id: str,
    slug: str,
    internal_text: str,
    export_text: str,
    status: LessonAcceptanceStatus,
    result: dict[str, int],
) -> None:
    """Idempotent single-slug import: write data + append a ledger entry."""
    export_hash = _sha(export_text)
    latest = latest_for_slug(acceptance_log, slug)
    if latest is not None and latest.export_hash == export_hash and latest.status == status:
        result["unchanged"] += 1
        return
    is_update = _write_internal_lesson(
        lessons_dir=lessons_dir,
        slug=slug,
        internal_text=internal_text,
    )
    _append_import_entry(
        acceptance_log=acceptance_log,
        slug=slug,
        run_id=run_id,
        internal_text=internal_text,
        export_text=export_text,
        status=status,
    )
    result["updated" if is_update else "created"] += 1


def _load_lesson_from_file(file: Path) -> Lesson:
    """Read + validate an external lesson file as an internal ``Lesson``."""
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LessonImportError(f"cannot read import file {file}: {exc}") from exc
    try:
        return Lesson.model_validate(payload)
    except ValidationError as exc:
        raise LessonImportError(f"invalid internal lesson {file}: {exc}") from exc


def import_lesson(
    file: Path,
    *,
    slug: str | None = None,
    status: LessonAcceptanceStatus = K_IMPORT_DEFAULT_STATUS,
    output_root: Path | None = None,
    repo_root: Path,
    run_id: str | None = None,
) -> dict[str, object]:
    """Import ONE externally-authored lesson file into ``data/lessons``."""
    file = Path(file)
    repo_root = Path(repo_root)
    write_root = Path(output_root) if output_root else repo_root
    resolved_slug = slug or file.stem
    run_id = run_id or uuid.uuid4().hex[:12]
    is_scratch = output_root is not None and write_root.resolve() != repo_root.resolve()
    acceptance_log = (
        (write_root / "lesson_acceptance_log.jsonl")
        if is_scratch
        else _acceptance_log_path(write_root)
    )

    lesson = _load_lesson_from_file(file)
    internal_text = lesson.model_dump_json(indent=2)
    export_text = json.dumps(lesson_to_export(lesson), ensure_ascii=False, indent=2) + "\n"

    lessons_dir = write_root / "data" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    acceptance_log.parent.mkdir(parents=True, exist_ok=True)

    result: dict[str, int] = {"created": 0, "unchanged": 0, "updated": 0}
    _import_one(
        lessons_dir=lessons_dir,
        acceptance_log=acceptance_log,
        run_id=run_id,
        slug=resolved_slug,
        internal_text=internal_text,
        export_text=export_text,
        status=status,
        result=result,
    )
    return {
        "slug": resolved_slug,
        "status": status,
        "internal_path": str((lessons_dir / f"{resolved_slug}.json").relative_to(write_root)),
        "export_path": f"dist/lessons/{resolved_slug}.json",
        "acceptance_log_path": str(acceptance_log),
        **result,
    }


def regenerate_dist(root: Path) -> dict[str, int]:
    """Derive ``dist/lessons/*.json`` FROM ``data/lessons/*.json`` (idempotent)."""
    root = Path(root)
    dist_dir = root / "dist" / "lessons"
    dist_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, int] = {"created": 0, "unchanged": 0, "updated": 0}
    for lesson_path in sorted((root / "data" / "lessons").glob("*.json")):
        lesson = Lesson.model_validate_json(lesson_path.read_text(encoding="utf-8"))
        export_text = json.dumps(lesson_to_export(lesson), ensure_ascii=False, indent=2) + "\n"
        export_path = dist_dir / lesson_path.name
        if export_path.exists() and export_path.read_text(encoding="utf-8") == export_text:
            result["unchanged"] += 1
            continue
        result["updated" if export_path.exists() else "created"] += 1
        export_path.write_text(export_text, encoding="utf-8")
    return result
