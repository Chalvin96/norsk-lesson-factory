"""Entry point: ``hydrate`` / ``LessonIndex``.

A sub-lesson-granularity catalog over committed lessons, backed by SQLite. It
stores each lesson's sub-units (objective statements, exercise prompts, section
spans) so callers can enumerate slugs and read a lesson's units cheaply.

Each row: ``(slug, unit_type, unit_id, text, source_file_hash)``. ``hydrate`` is
content-hashed and idempotent: re-running it with unchanged lesson files is a
no-op. Semantic placement (which existing lesson a request belongs to) is done
by the LLM triage step over this catalog, not by an embedding stored here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

K_INDEX_DB_PATH = Path(__file__).resolve().parents[4] / "store" / "index.db"

UnitType = str  # "objective" | "explanation_span" | "exercise"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(db_path))


def _file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LessonIndex:
    """Sub-lesson-granularity index over committed lessons."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or K_INDEX_DB_PATH
        self._conn = _connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lesson_units (
                slug        TEXT NOT NULL,
                unit_type   TEXT NOT NULL,
                unit_id     TEXT NOT NULL,
                text        TEXT NOT NULL,
                file_hash   TEXT NOT NULL,
                PRIMARY KEY (slug, unit_type, unit_id)
            );
            CREATE TABLE IF NOT EXISTS file_hashes (
                slug      TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def hydrate(self, lessons_root: Path) -> dict[str, int]:
        """Index all lessons under ``lessons_root``. Content-hashed, idempotent.

        Also deletes DB rows for any slug previously indexed but no longer
        present under ``lessons_root`` -- otherwise ``slugs()`` keeps returning
        lessons that were removed from disk.
        """
        result = {"created": 0, "unchanged": 0, "updated": 0, "deleted": 0}
        present_slugs: set[str] = set()
        for p in sorted(lessons_root.glob("*.json")):
            text = p.read_text(encoding="utf-8")
            fhash = _file_hash(text)
            slug = p.stem
            present_slugs.add(slug)
            existing = self._conn.execute(
                "SELECT file_hash FROM file_hashes WHERE slug = ?", (slug,)
            ).fetchone()
            if existing and existing[0] == fhash:
                result["unchanged"] += 1
                continue
            if existing:
                self._delete_slug(slug)
                result["updated"] += 1
            else:
                result["created"] += 1
            lesson = json.loads(text)
            self._index_lesson(slug, lesson, fhash)

        indexed_slugs = {
            row[0] for row in self._conn.execute("SELECT slug FROM file_hashes").fetchall()
        }
        for stale_slug in indexed_slugs - present_slugs:
            self._delete_slug(stale_slug)
            result["deleted"] += 1

        self._conn.commit()
        return result

    def _index_lesson(self, slug: str, lesson: dict[str, Any], fhash: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO file_hashes (slug, file_hash) VALUES (?, ?)",
            (slug, fhash),
        )
        for obj in lesson.get("objectives", []):
            self._insert_unit(slug, "objective", obj.get("id", ""), obj.get("statement", ""), fhash)
        for el in lesson.get("elements", []):
            if el.get("element_kind") == "exercise":
                prompt_text = _spans_text(el.get("prompt", []))
                self._insert_unit(slug, "exercise", el.get("id", ""), prompt_text, fhash)
            elif el.get("element_kind") == "section":
                for block in el.get("blocks", []):
                    block_text = _block_text(block)
                    if block_text:
                        self._insert_unit(
                            slug,
                            "explanation_span",
                            f"{el.get('id', '')}#{block.get('kind', '')}",
                            block_text,
                            fhash,
                        )

    def _insert_unit(self, slug: str, unit_type: str, unit_id: str, text: str, file_hash: str) -> None:
        if not text.strip():
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO lesson_units (slug, unit_type, unit_id, text, file_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (slug, unit_type, unit_id, text, file_hash),
        )

    def _delete_slug(self, slug: str) -> None:
        self._conn.execute("DELETE FROM lesson_units WHERE slug = ?", (slug,))
        self._conn.execute("DELETE FROM file_hashes WHERE slug = ?", (slug,))

    def slugs(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT slug FROM lesson_units ORDER BY slug").fetchall()
        return [r[0] for r in rows]

    def units_for_slug(self, slug: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT unit_type, unit_id, text FROM lesson_units WHERE slug = ? ORDER BY unit_type, unit_id",
            (slug,),
        ).fetchall()
        return [{"unit_type": r[0], "unit_id": r[1], "text": r[2]} for r in rows]

    def close(self) -> None:
        self._conn.close()


def hydrate(lessons_root: Path, db_path: Path | None = None) -> dict[str, int]:
    """Content-hashed hydrate of the lesson index. Idempotent."""
    idx = LessonIndex(db_path)
    try:
        return idx.hydrate(lessons_root)
    finally:
        idx.close()


def _spans_text(spans: Any) -> str:
    if isinstance(spans, str):
        return spans
    if not isinstance(spans, list):
        return ""
    parts: list[str] = []
    for span in spans:
        if isinstance(span, dict):
            parts.append(str(span.get("value", "")))
        else:
            parts.append(str(span))
    return "".join(parts)


def _block_text(block: dict[str, Any]) -> str:
    kind = block.get("kind", "")
    if kind == "prose":
        return _spans_text(block.get("spans", []))
    if kind == "table":
        cells = block.get("rows", [])
        return " ".join(str(c) for row in cells for c in row)
    if kind == "example":
        return _spans_text(block.get("source", [])) + " " + _spans_text(block.get("gloss", []))
    return ""


__all__ = [
    "LessonIndex",
    "hydrate",
    "K_INDEX_DB_PATH",
]
