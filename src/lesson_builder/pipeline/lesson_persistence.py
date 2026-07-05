"""Entry point: ``default_exporter``.

Durable export/persistence sink for the Lesson-QA graph. Validate-then-write
machinery for internal + dist + acceptance ledger + manifest upsert, behind the
existing ``Exporter`` Protocol (see ``lesson_qa_graph.py``). This module holds
no graph topology; the graph imports ``default_exporter`` as the default
``GraphDeps.exporter``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.lesson_acceptance_log import (
    LessonAcceptanceEntry,
    append_entry,
    latest_for_slug,
)
from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.schema import Lesson

K_CURRICULUM_STRUCTURE_PATH = Path("curriculum") / "structure.json"
K_DATA_CURRICULUM_STRUCTURE_PATH = Path("data") / "curriculum" / "structure.json"


def default_exporter(
    *,
    slug: str,
    lesson: dict[str, Any],
    run_id: str,
    signoff_score: float | None,
    blocking_issues_messages: list[str],
    corrections_applied: list[str],
    repo_root: Path,
    acceptance_log_path: Path,
    override: bool = False,
    output_root: Path | None = None,
) -> str:
    """Validate, then atomically persist internal + dist, then append the ledger.

    ``output_root`` (when set) is where ``data/lessons/<slug>.json`` and
    ``dist/lessons/<slug>.json`` are WRITTEN -- e.g. a scratch dir under
    ``store/`` for ad-hoc/demo runs that should not dirty the tracked repo
    tree. It defaults to ``repo_root`` so existing real-repo callers are
    unaffected. The loader always reads from ``repo_root`` regardless; only
    the write target is redirected here. ``export_path`` is reported relative
    to whichever root the files actually landed under.

    Ordering is load-bearing, not incidental:

    0. Idempotency check FIRST (design §12): if the latest ledger entry for
       this slug already has the same ``source_lesson_hash`` we are about to
       write, the content is already exported and recorded -- this call is a
       no-op. Nothing is re-written (no dist/internal write, no manifest
       touch, no duplicate ledger line); the prior export path is returned
       as-is.
    1. Validate + project to the export shape ``lesson_to_export`` over
       ``_validate_lesson``). If validation raises, NOTHING below has run --
       the prior ``data/lessons/<slug>.json`` is untouched, no dist file is
       written, and no ledger entry is appended. A failed export cannot
       corrupt the internal store.
    2. Write dist, then internal, each via temp-file-in-same-dir +
       ``os.replace`` (atomic rename on POSIX) -- a crash mid-write leaves
       either the old file or the fully-written new file, never a partial one.
    3. Append the ledger entry LAST. If the process crashes between step 2 and
       the ledger append, both files are already fully and atomically written;
       re-running export_node is idempotent (it overwrites both files again and
       appends a fresh ledger line), so a crash never leaves an exported file
       without eventual provenance.
    4. Upsert the manifest (design §12) after a real export. The manifest is
       only ever written under ``write_root`` -- in scratch mode that is the
       scratch dir, never the tracked ``dist/manifest.json``.
    """
    repo_root = Path(repo_root)
    write_root = Path(output_root) if output_root else repo_root

    export = lesson_to_export(_validate_lesson(lesson))
    export_text = json.dumps(export, ensure_ascii=False, indent=2) + "\n"
    internal_text = json.dumps(lesson, ensure_ascii=False, indent=2) + "\n"
    source_lesson_hash = _sha(internal_text)

    existing = latest_for_slug(Path(acceptance_log_path), slug)
    if existing is not None and existing.source_lesson_hash == source_lesson_hash:
        return existing.export_path

    export_path = write_root / "dist" / "lessons" / f"{slug}.json"
    internal_path = write_root / "data" / "lessons" / f"{slug}.json"
    _atomic_write_text(export_path, export_text)
    _atomic_write_text(internal_path, internal_text)

    entry = LessonAcceptanceEntry(
        slug=slug,
        ts=_now_iso(),
        run_id=run_id or None,
        source_kind="graph_export",
        source_lesson_hash=source_lesson_hash,
        signoff_score=signoff_score,
        reviewer="human",
        status="accepted_override" if override else "accepted",
        blocking_issues=blocking_issues_messages,
        corrections_applied=corrections_applied,
        export_path=str(export_path.relative_to(write_root)),
        export_hash=_sha(export_text),
    )
    append_entry(Path(acceptance_log_path), entry)
    _upsert_manifest_entry(
        write_root,
        repo_root=repo_root,
        slug=slug,
        export=export,
        export_path=export_path,
    )
    return str(export_path.relative_to(write_root))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _upsert_manifest_entry(
    write_root: Path,
    *,
    repo_root: Path,
    slug: str,
    export: dict[str, Any],
    export_path: Path,
) -> None:
    """Upsert one canonical ``lessons[]`` entry for ``slug`` in ``dist/manifest.json``.

    Mirrors the approach of the original bulk importer
    (preserve existing fields/provenance, keep ``lesson_count`` consistent)
    but only ever touches the single exported slug's entry instead of
    rebuilding the whole manifest from ``dist/lessons/*.json`` -- the exporter
    only knows about one lesson per call. ``write_root`` is the scratch-vs-repo
    write target the rest of the exporter already uses, so scratch-mode runs
    never touch the tracked ``dist/manifest.json``.
    """
    manifest_path = write_root / "dist" / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    lessons: list[dict[str, Any]] = manifest.get("lessons", [])
    existing_entry = next((entry for entry in lessons if entry.get("slug") == slug), None)
    new_entry = {
        "slug": slug,
        "path": str(export_path.relative_to(write_root)),
        "source": (existing_entry or {}).get("source", f"graph_export/{slug}.json"),
        "title": export["title"],
        "cefr_level": export["cefr_level"],
    }
    other_lessons = [entry for entry in lessons if entry.get("slug") != slug]
    lesson_order = _lesson_order_by_slug(repo_root)
    updated_lessons = _sort_manifest_lessons(
        [*other_lessons, new_entry],
        lesson_order=lesson_order,
    )
    updated_manifest = {
        "schema_version": manifest.get("schema_version", "3.0"),
        "lesson_count": len(updated_lessons),
        "lessons": updated_lessons,
        **({"duplicates": manifest["duplicates"]} if "duplicates" in manifest else {}),
    }
    manifest_text = json.dumps(updated_manifest, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(manifest_path, manifest_text)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via temp-file-in-same-dir + atomic rename.

    Writing to a sibling temp file and ``os.replace``-ing it into place means a
    crash mid-write leaves either the prior file intact or the fully-written
    new one -- never a truncated/partial file. ``fsync`` on the temp file
    before rename, plus a best-effort directory fsync after, gives durability
    against a crash that lands between the write and the rename being
    persisted to disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_dir_best_effort(path.parent)


def _fsync_dir_best_effort(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _validate_lesson(lesson: dict[str, Any]) -> Any:
    """Validate once before projecting to export (the gate already did this)."""
    return Lesson.model_validate(lesson)


def _lesson_order_by_slug(repo_root: Path) -> dict[str, int]:
    """Return curriculum lesson order by slug, supporting both repo shapes.

    Preference order:
    1. ``curriculum/structure.json`` with chapter-based ``lessons[].key`` order.
    2. ``data/curriculum/structure.json`` with either chapter-based order or the
       curriculum flow's simpler ``topics[].slug`` order.

    When no recognized structure exists, returns an empty mapping so manifest
    ordering falls back to preserving current manifest order for known entries.
    """
    for relative_path in (K_CURRICULUM_STRUCTURE_PATH, K_DATA_CURRICULUM_STRUCTURE_PATH):
        structure_path = repo_root / relative_path
        if not structure_path.exists():
            continue
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        lesson_order = _extract_lesson_order(structure)
        if lesson_order:
            return lesson_order
    return {}


def _extract_lesson_order(structure: dict[str, Any]) -> dict[str, int]:
    """Parse supported curriculum structure formats into slug -> rank."""
    chapters = structure.get("chapters")
    if isinstance(chapters, dict):
        ordered_slugs: list[str] = []
        for chapter_payload in chapters.values():
            lessons = chapter_payload.get("lessons", []) if isinstance(chapter_payload, dict) else []
            for lesson_entry in lessons:
                if isinstance(lesson_entry, dict) and isinstance(lesson_entry.get("key"), str):
                    ordered_slugs.append(lesson_entry["key"])
        if ordered_slugs:
            return {lesson_slug: index for index, lesson_slug in enumerate(ordered_slugs)}

    topics = structure.get("topics")
    if isinstance(topics, list):
        ordered_slugs = [
            topic["slug"]
            for topic in topics
            if isinstance(topic, dict) and isinstance(topic.get("slug"), str)
        ]
        if ordered_slugs:
            return {lesson_slug: index for index, lesson_slug in enumerate(ordered_slugs)}
    return {}


def _sort_manifest_lessons(
    lessons: list[dict[str, Any]],
    *,
    lesson_order: dict[str, int],
) -> list[dict[str, Any]]:
    """Sort manifest lessons by curriculum order, preserving unknown-entry order."""
    indexed_lessons = list(enumerate(lessons))
    sorted_pairs = sorted(
        indexed_lessons,
        key=lambda indexed_entry: _manifest_sort_key(
            indexed_entry[1],
            lesson_order=lesson_order,
            original_index=indexed_entry[0],
        ),
    )
    return [entry for _, entry in sorted_pairs]


def _manifest_sort_key(
    manifest_entry: dict[str, Any],
    *,
    lesson_order: dict[str, int],
    original_index: int,
) -> tuple[int, int]:
    slug = manifest_entry.get("slug")
    if isinstance(slug, str) and slug in lesson_order:
        return (0, lesson_order[slug])
    return (1, original_index)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
