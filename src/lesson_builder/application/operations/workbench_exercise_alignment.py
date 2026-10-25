"""Entry point: `ExerciseAlignment.states`, `capture`, `pin`, `restore_pin`."""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import TypeAdapter

from lesson_builder.application.operations.load_exercises import parse_exercise_source
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import WorkbenchSourceNotFound
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentLog
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPin
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPinChange
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentRecord
from lesson_builder.application.operations.workbench_records import ExerciseSectionChange
from lesson_builder.application.operations.workbench_records import KeepReason
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS
from lesson_builder.domain.lesson.models.elements import Exercise
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.formats.workbench_source import summary_source_text
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record


class ExerciseAlignment:
    """Read alignment state, capture typed pins and persist owner decisions for one lesson."""

    def __init__(self, root: Path, lesson_id: str) -> None:
        self.root = root
        self.lesson_id = lesson_id

    def states(self, source: WorkbenchSource) -> tuple[ExerciseAlignmentState, ...]:
        """Project every current exercise against its latest explicit section pin."""
        try:
            exercises = parse_exercise_source(source.files["exercises.yaml"]).exercises
        except (TypeError, ValueError, yaml.YAMLError) as error:
            return (ExerciseUnknown(exercise_key=None, reason=f"exercises.yaml could not be parsed: {error}"),)

        current_sections = _section_sources_by_id(split_lesson_source(source.files["lesson.md"]).blocks)
        local_log = read_workbench_record(self.root, "exercise_alignments", self.lesson_id, ExerciseAlignmentLog)
        canonical = self._load_canonical_exercise_alignments()
        histories: dict[str, list[ExerciseAlignmentRecord]] = {}
        for record in local_log.records:
            histories.setdefault(record.exercise_key, []).append(record)
        return tuple(
            _exercise_alignment_state(
                exercise,
                tuple(sorted(histories.get(exercise.id, ()), key=lambda item: item.at, reverse=True)),
                canonical.get(exercise.id),
                current_sections,
                source=source,
            )
            for exercise in exercises
        )

    def capture(self, source: WorkbenchSource, keys: list[str]) -> dict[str, ExerciseAlignmentPin]:
        """Capture exact section evidence and visible-text summaries for selected exercises."""
        exercises = {
            exercise.id: exercise for exercise in parse_exercise_source(source.files["exercises.yaml"]).exercises
        }
        blocks = {block.key: block for block in source.blocks if block.kind == "exercise"}
        sections = _section_sources_by_id(split_lesson_source(source.files["lesson.md"]).blocks)
        state_by_key = {state.exercise_key: state for state in self.states(source) if state.exercise_key is not None}
        captured: dict[str, ExerciseAlignmentPin] = {}
        for key in keys:
            exercise = exercises.get(key)
            block = blocks.get(key)
            if exercise is None or block is None:
                raise ValueError("Exercise not found")
            section_ids = {reference.section_id for reference in exercise.derived_from}
            section_sources = {section_id: sections.get(section_id, "") for section_id in sorted(section_ids)}
            state = state_by_key.get(key)
            changed_sections = state.changed_sections if isinstance(state, ExerciseStale) else ()
            captured[key] = ExerciseAlignmentPin(
                exercise_key=key,
                checked_against={section_id: _section_hash(text) for section_id, text in section_sources.items()},
                section_sources=section_sources,
                changed_sections=[
                    ExerciseAlignmentPinChange(
                        section_id=change.section_id,
                        before_text=change.before_text,
                        after_text=change.after_text,
                        summary=change.summary,
                    )
                    for change in changed_sections
                ],
                current_exercise_source=block.source,
            )
        return captured

    def pin(
        self,
        pin: ExerciseAlignmentPin,
        outcome: Literal["regenerated", "kept"],
        note: str | None = None,
        decision_id: str | None = None,
        *,
        reason: KeepReason | None = None,
        comment: str | None = None,
    ) -> ExerciseAlignmentRecord:
        """Persist a captured alignment, including automated regeneration outcomes."""
        if len(comment if comment is not None else note or "") > K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS:
            raise ValueError(
                f"Decision comment must be at most {K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS} characters."
            )
        source = read_workbench_source(self.root, self.lesson_id)
        record = ExerciseAlignmentRecord(
            exercise_key=pin["exercise_key"],
            checked_against=pin["checked_against"],
            section_sources=pin["section_sources"],
            outcome=outcome,
            canonical_sha256=source.started_from,
            draft_created_at=source.created_at,
            note=note,
            reason=reason,
            comment=comment,
            at=datetime.now(UTC).isoformat(),
            decision_id=decision_id,
        )
        log = read_workbench_record(self.root, "exercise_alignments", self.lesson_id, ExerciseAlignmentLog)
        if decision_id:
            log.records = [item for item in log.records if item.decision_id != decision_id]
        log.records.append(record)
        write_workbench_record(self.root, "exercise_alignments", self.lesson_id, log)
        return record

    def keep_current(
        self,
        source: WorkbenchSource,
        exercise_key: str,
        note: str = "",
        *,
        reason: KeepReason | None = None,
        comment: str | None = None,
    ) -> ExerciseAlignmentRecord:
        """Record an explicit owner reason and current section pins for a stale exercise."""
        if len(comment if comment is not None else note or "") > K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS:
            raise ValueError(
                f"Decision comment must be at most {K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS} characters."
            )
        if reason is None:
            raise ValueError("Choose a reason for this owner decision.")
        state = next(
            (
                item
                for item in self.states(source)
                if isinstance(item, ExerciseStale) and item.exercise_key == exercise_key
            ),
            None,
        )
        if state is None:
            raise ValueError("Exercise is not currently stale")
        return self.pin(
            self.capture(source, [exercise_key])[exercise_key],
            "kept",
            note.strip() or None,
            reason=KeepReason(reason),
            comment=(comment if comment is not None else note).strip() or None,
        )

    def record_paths(self) -> tuple[Path, ...]:
        """Return every persisted path this alignment operation may update."""
        return (WorkspacePaths(self.root).workbench_root / "exercise_alignments" / f"{self.lesson_id}.json",)

    def _load_canonical_exercise_alignments(self) -> dict[str, ExerciseAlignmentRecord]:
        try:
            canonical = read_workbench_source(self.root, self.lesson_id, canonical_only=True)
            exercises = parse_exercise_source(canonical.files["exercises.yaml"]).exercises
        except (WorkbenchSourceNotFound, OSError, TypeError, ValueError, yaml.YAMLError):
            return {}
        sections = _section_sources_by_id(split_lesson_source(canonical.files["lesson.md"]).blocks)
        return {
            exercise.id: _alignment_record(
                exercise.id,
                {reference.section_id for reference in exercise.derived_from},
                sections,
                "canonical",
                at="1970-01-01T00:00:00+00:00",
            )
            for exercise in exercises
        }


@dataclass(frozen=True)
class ExerciseAligned:
    """A linked exercise matches every section version it was checked against."""

    exercise_key: str
    alignment: ExerciseAlignmentRecord
    history: tuple[ExerciseAlignmentRecord, ...] = ()
    status: Literal["aligned"] = "aligned"


@dataclass(frozen=True)
class ExerciseStale:
    """A linked exercise references one or more changed sections."""

    exercise_key: str
    changed_sections: tuple[ExerciseSectionChange, ...]
    alignment: ExerciseAlignmentRecord
    history: tuple[ExerciseAlignmentRecord, ...] = ()
    status: Literal["stale"] = "stale"


@dataclass(frozen=True)
class ExerciseUntracked:
    """An exercise has no authored section alignment."""

    exercise_key: str
    history: tuple[ExerciseAlignmentRecord, ...] = ()
    alignment: ExerciseAlignmentRecord | None = None
    status: Literal["untracked"] = "untracked"


@dataclass(frozen=True)
class ExerciseUnknown:
    """The workbench cannot determine alignment from the available source."""

    exercise_key: str | None
    reason: str
    history: tuple[ExerciseAlignmentRecord, ...] = ()
    status: Literal["unknown"] = "unknown"


type ExerciseAlignmentState = ExerciseAligned | ExerciseStale | ExerciseUntracked | ExerciseUnknown


def restore_pin(exercise_key: str, value: object) -> ExerciseAlignmentPin:
    """Parse a JSON checkpoint pin, migrating the earlier keyless capture shape."""
    if not isinstance(value, Mapping):
        raise TypeError("Exercise alignment checkpoint Pin must be an object")
    payload = dict(value)
    payload.setdefault("exercise_key", exercise_key)
    return TypeAdapter(ExerciseAlignmentPin).validate_python(payload)


def _section_sources_by_id(blocks: tuple[SourceBlock, ...]) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_section: str | None = None
    for block in blocks:
        if block.kind == "heading":
            current_section = block.key
            sections[current_section] = block.source
        elif current_section is not None:
            sections[current_section] += block.source
    return sections


def _section_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _alignment_record(
    exercise_key: str,
    section_ids: set[str],
    sections: dict[str, str],
    outcome: Literal["canonical", "regenerated", "kept"],
    *,
    note: str | None = None,
    at: str | None = None,
    reason: KeepReason | None = None,
    comment: str | None = None,
) -> ExerciseAlignmentRecord:
    section_sources = {key: sections.get(key, "") for key in section_ids}
    return ExerciseAlignmentRecord(
        exercise_key=exercise_key,
        checked_against={key: _section_hash(value) for key, value in section_sources.items()},
        section_sources=section_sources,
        outcome=outcome,
        note=note,
        reason=reason,
        comment=comment,
        at=at or datetime.now(UTC).isoformat(),
    )


def _changed_exercise_sections(
    alignment: ExerciseAlignmentRecord, references: set[str], current_sections: dict[str, str]
) -> tuple[ExerciseSectionChange, ...]:
    changed = []
    for section_id in sorted(references | alignment.checked_against.keys()):
        current_text = current_sections.get(section_id)
        if _section_hash(current_text or "") == alignment.checked_against.get(section_id):
            continue
        before_text = alignment.section_sources.get(section_id)
        changed.append(
            ExerciseSectionChange(
                section_id=section_id,
                before_text=before_text,
                after_text=current_text,
                summary=_section_change_summary(section_id, before_text, current_text),
            )
        )
    return tuple(changed)


def _exercise_alignment_state(
    exercise: Exercise,
    history: tuple[ExerciseAlignmentRecord, ...],
    canonical: ExerciseAlignmentRecord | None,
    current_sections: dict[str, str],
    *,
    source: WorkbenchSource,
) -> ExerciseAlignmentState:
    references = {reference.section_id for reference in exercise.derived_from}
    eligible = tuple(
        record
        for record in history
        if source.exists
        and record.canonical_sha256 == source.started_from
        and (
            source.started_from is not None
            or (source.created_at is not None and record.draft_created_at == source.created_at)
        )
    )
    alignment = max((*eligible, *((canonical,) if canonical else ())), key=lambda item: item.at, default=None)
    if not references and (alignment is None or not alignment.checked_against):
        return ExerciseUntracked(exercise_key=exercise.id, history=history, alignment=alignment)
    if alignment is None:
        return ExerciseUnknown(
            exercise_key=exercise.id,
            reason="No canonical version or local section comparison is available for this exercise.",
            history=history,
        )
    changed = _changed_exercise_sections(alignment, references, current_sections)
    if changed:
        return ExerciseStale(exercise_key=exercise.id, changed_sections=changed, alignment=alignment, history=history)
    if not references:
        return ExerciseUntracked(exercise_key=exercise.id, history=history, alignment=alignment)
    return ExerciseAligned(exercise_key=exercise.id, alignment=alignment, history=history)


def _section_change_summary(section_id: str, before: str | None, after: str | None) -> str:
    if before is None:
        return f"Section {section_id} was added."
    if after is None:
        return f"Section {section_id} was removed."
    removed, added = _changed_section_words(before, after)
    if not removed and not added:
        before_lines = len(before.splitlines())
        after_lines = len(after.splitlines())
        if after_lines > before_lines:
            change = "was split across lines"
        elif after_lines < before_lines:
            change = "was joined across lines"
        else:
            change = "formatting changed"
        return f"Section {section_id} {change}; wording is unchanged."
    return _word_change_summary(section_id, removed, added)


def _changed_section_words(before: str, after: str) -> tuple[list[str], list[str]]:
    before_words = summary_source_text(before).split()
    after_words = summary_source_text(after).split()
    changes = difflib.SequenceMatcher(a=before_words, b=after_words, autojunk=False).get_opcodes()
    removed = [
        word for tag, start, end, _, _ in changes if tag in ("delete", "replace") for word in before_words[start:end]
    ]
    added = [
        word for tag, _, _, start, end in changes if tag in ("insert", "replace") for word in after_words[start:end]
    ]
    return removed, added


def _word_change_summary(section_id: str, removed: list[str], added: list[str]) -> str:
    if len(removed) == 1 and not added:
        return f"Removed “{removed[0]}” from section {section_id}."
    if len(added) == 1 and not removed:
        return f"Added “{added[0]}” to section {section_id}."
    if len(removed) == 1 and len(added) == 1:
        return f"Changed “{removed[0]}” to “{added[0]}” in section {section_id}."
    old_excerpt = " ".join(removed[:5])
    new_excerpt = " ".join(added[:5])
    return f"Section {section_id} wording changed: “{old_excerpt}” → “{new_excerpt}”."
