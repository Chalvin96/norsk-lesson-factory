from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal

from pydantic import BaseModel, Field

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

LessonAcceptanceStatus = Literal[
    "imported_unverified",
    "curated",
    "accepted",
    "accepted_human_edit",
    "accepted_override",
    "deferred",
    "superseded",
]
SourceKind = Literal["dist_import", "external_import", "graph_export"]
_BASELINE_STATUSES = ("curated", "accepted", "accepted_human_edit")
_DEMOTING_STATUSES = ("imported_unverified", "superseded")


class LessonAcceptanceEntry(BaseModel):
    slug: str
    ts: str | None = None
    # provenance
    run_id: str | None = None
    schema_version: str = "3.0"
    source_kind: SourceKind
    supersedes_export_hash: str | None = None
    # hashes / review
    source_lesson_hash: str
    requirements_hash: str | None = None
    gate_hash: str | None = None
    answer_review_hash: str | None = None
    pedagogy_hash: str | None = None
    signoff_score: float | None = None
    reviewer: str
    status: LessonAcceptanceStatus
    blocking_issues: list[str] = Field(default_factory=list)
    corrections_applied: list[str] = Field(default_factory=list)
    model_calls: list[dict[str, Any]] = Field(default_factory=list)
    export_path: str
    export_hash: str

    def usable_as_regression_baseline(self) -> bool:
        return self.status in _BASELINE_STATUSES


@contextmanager
def _locked_append_handle(log_path: Path) -> Iterator[IO[str]]:
    """Open ``log_path`` for read+append, holding a best-effort advisory lock.

    Opened in ``"a+"`` mode (readable, writes always go to EOF) so callers that
    need to read-then-append (``mark_unverified``) can do so under one lock
    without a separate handle. The lock is exclusive (``LOCK_EX``) for the
    lifetime of the handle, so concurrent ``append_entry``/``mark_unverified``
    callers serialize instead of interleaving writes. Locking is best-effort:
    platforms without ``fcntl`` (non-POSIX) silently skip it rather than fail
    the write.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a+", encoding="utf-8") as fh:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def append_entry(log_path: Path, entry: LessonAcceptanceEntry) -> None:
    """Append one ledger line durably: write, flush, and fsync before returning.

    The advisory lock is held for the duration of the write so a concurrent
    appender cannot interleave a partial line into this one.
    """
    with _locked_append_handle(log_path) as fh:
        fh.write(entry.model_dump_json() + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _parse_entries(text: str) -> list[LessonAcceptanceEntry]:
    """Parse ledger lines, tolerating a single malformed trailing line.

    A torn trailing line (e.g. a write interrupted mid-flush) is dropped rather
    than failing the whole read -- it carries no entry anyone has observed yet.
    Any other malformed line (not the last one) is real corruption and still
    raises.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    entries: list[LessonAcceptanceEntry] = []
    for index, line in enumerate(lines):
        is_last = index == len(lines) - 1
        try:
            entries.append(LessonAcceptanceEntry.model_validate_json(line))
        except ValueError:
            if not is_last:
                raise
            # Quarantine a torn trailing line instead of failing the whole read.
    return entries


def read_entries(log_path: Path) -> list[LessonAcceptanceEntry]:
    """Read and parse every ledger line (see ``_parse_entries`` for tolerance)."""
    if not log_path.exists():
        return []
    return _parse_entries(log_path.read_text(encoding="utf-8"))


def latest_for_slug(log_path: Path, slug: str) -> LessonAcceptanceEntry | None:
    matches = [e for e in read_entries(log_path) if e.slug == slug]
    return matches[-1] if matches else None


def latest_regression_baseline(log_path: Path, slug: str) -> LessonAcceptanceEntry | None:
    """The regression-baseline reader.

    Walks the slug's entries newest-first. A demoting status
    (``imported_unverified`` / ``superseded``) on the *newest* entry wins
    outright and overrides any older baseline-eligible entry for the same
    slug -- an explicit demotion must take effect even though the log is
    append-only and an older ``accepted`` entry still exists. A non-baseline,
    non-demoting status (e.g. ``deferred``) does not demote; it is skipped
    while we keep walking back to find the most recent baseline-eligible
    entry.
    """
    matches = [e for e in read_entries(log_path) if e.slug == slug]
    for entry in reversed(matches):
        if entry.status in _DEMOTING_STATUSES:
            return None
        if entry.usable_as_regression_baseline():
            return entry
    return None


def mark_unverified(
    log_path: Path,
    slug: str,
    *,
    reviewer: str,
    reason: str,
) -> LessonAcceptanceEntry:
    """Append an ``imported_unverified`` entry that demotes ``slug``.

    Demotion is append-only: prior lines are never rewritten. The new entry
    copies provenance fields (``export_path``, ``export_hash``,
    ``source_lesson_hash``, ``source_kind``, plus the review hashes
    ``requirements_hash``/``gate_hash``/``answer_review_hash``/``pedagogy_hash``)
    from the slug's latest existing entry so the demotion stays traceable to a
    real export and does not silently drop provenance.

    The read (find the latest entry) and the append are performed under a
    single advisory lock so a concurrent ``append_entry``/``mark_unverified``
    cannot race between the read and the write.
    """
    with _locked_append_handle(log_path) as fh:
        fh.seek(0)
        text = fh.read()
        entries = _parse_entries(text)
        matches = [entry for entry in entries if entry.slug == slug]
        latest = matches[-1] if matches else None
        if latest is None:
            raise ValueError(
                f"cannot mark_unverified for unknown slug {slug!r}: "
                "no existing acceptance entry to demote"
            )
        entry = LessonAcceptanceEntry(
            slug=slug,
            ts=datetime.now(UTC).isoformat(),
            source_kind=latest.source_kind,
            source_lesson_hash=latest.source_lesson_hash,
            requirements_hash=latest.requirements_hash,
            gate_hash=latest.gate_hash,
            answer_review_hash=latest.answer_review_hash,
            pedagogy_hash=latest.pedagogy_hash,
            reviewer=reviewer,
            status="imported_unverified",
            blocking_issues=[reason],
            export_path=latest.export_path,
            export_hash=latest.export_hash,
        )
        fh.seek(0, os.SEEK_END)
        fh.write(entry.model_dump_json() + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return entry
