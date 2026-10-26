"""Entry point: `build_curriculum_payload`, `build_curriculum_checks`, `build_lesson_payload`, `build_source_payload`, `build_check_payload` for workbench HTTP.

Both project application results into camelCase JSON for the React workbench.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import fields
from dataclasses import is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import cast

import yaml
from pydantic import BaseModel
from pydantic import TypeAdapter

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.workbench_audio import project_workbench_audio
from lesson_builder.application.operations.workbench_curriculum import CurriculumFinding
from lesson_builder.application.operations.workbench_curriculum import check_curriculum
from lesson_builder.application.operations.workbench_curriculum import find_stale_curriculum_entries
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_lessons import LessonPackageRef
from lesson_builder.application.operations.workbench_lessons import WorkbenchLesson
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import count_workbench_checks
from lesson_builder.application.operations.workbench_lessons import list_workbench_lessons
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_proposals import check_preview
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_records import StoredChatTurn
from lesson_builder.application.operations.workbench_review import finding_label
from lesson_builder.application.operations.workbench_state import BlockTarget
from lesson_builder.application.operations.workbench_state import StatusChip
from lesson_builder.application.operations.workbench_state import WorkbenchFinding
from lesson_builder.application.operations.workbench_state import build_curriculum_lesson_status_chips
from lesson_builder.application.operations.workbench_state import build_workbench_lesson_state
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.web.workbench_wire import WorkbenchAudioClipStatus
from lesson_builder.web.workbench_wire import WorkbenchAudioStatus
from lesson_builder.web.workbench_wire import WorkbenchAudioVoice
from lesson_builder.web.workbench_wire import WorkbenchCheckResult
from lesson_builder.web.workbench_wire import WorkbenchCurriculumChecks
from lesson_builder.web.workbench_wire import WorkbenchCurriculumEntry
from lesson_builder.web.workbench_wire import WorkbenchCurriculumPayload
from lesson_builder.web.workbench_wire import WorkbenchDraftStatus
from lesson_builder.web.workbench_wire import WorkbenchLessonHeader
from lesson_builder.web.workbench_wire import WorkbenchLessonPayload
from lesson_builder.web.workbench_wire import WorkbenchLessonState
from lesson_builder.web.workbench_wire import WorkbenchPlanSource
from lesson_builder.web.workbench_wire import WorkbenchSourceBlock
from lesson_builder.web.workbench_wire import WorkbenchSourceFinding
from lesson_builder.web.workbench_wire import WorkbenchSourcePayload
from lesson_builder.web.workbench_wire import WorkbenchStatusChip
from lesson_builder.workspace.workbench_store import read_workbench_record

K_WEB_CAMEL_BOUNDARY = re.compile(r"_([a-z0-9])")


@dataclass(frozen=True)
class _PlanSourceProjection:
    """Parsed author plan values with an exact-source recovery path."""

    frontmatter: dict[str, object]
    body: str
    parse_error: str | None


def build_curriculum_payload(repo_root: Path) -> WorkbenchCurriculumPayload:
    """Return cached curriculum state, invalidated by source and record file metadata."""
    root = repo_root.resolve()
    signature = _curriculum_projection_signature(root)
    return deepcopy(_cached_curriculum_payload(str(root), signature))


def build_curriculum_checks(repo_root: Path) -> WorkbenchCurriculumChecks:
    """Count each lesson's deterministic checks exactly as its lesson page does."""
    counts = {
        lesson.lesson_id: count_workbench_checks(resolve_lesson_package(repo_root, lesson.lesson_id))
        for lesson in list_workbench_lessons(repo_root)
        if lesson.source != "missing"
    }
    return {"counts": counts}


def build_lesson_payload(repo_root: Path, package: LessonPackageRef) -> WorkbenchLessonPayload:
    """Return one lesson's compiled view, source text, source findings and current pane state."""
    view = build_workbench_preview(package)
    packet = view.packet
    source = read_workbench_source(repo_root, package.lesson_id)
    check = build_check_payload(view, source)
    audio_projection = (
        project_workbench_audio(repo_root, package.lesson_id, lesson=view.lesson)
        if packet is not None
        else {"clips": [], "voices": [], "missing_count": 0, "stale_count": 0, "ready_count": 0, "blockers": []}
    )
    state = build_workbench_lesson_state(
        repo_root,
        package.lesson_id,
        source,
        tuple(check["notes"]),
        check_preview(view).status == "failed",
        unmatched_check_count=len(check["findings"]) + len(check["errors"]),
        audio_projection=audio_projection,
    )
    audio_payload = _build_audio_payload(audio_projection)
    state_payload = cast(WorkbenchLessonState, serialize_json(state))
    plan_projection = cast(WorkbenchPlanSource, serialize_json(_project_plan_source(view.plan_source)))
    return {
        "lessonId": package.lesson_id,
        "source": package.source,
        "sourceLabel": view.source_label,
        "lessonSource": view.lesson_source,
        "planSource": view.plan_source,
        "planPreview": plan_projection,
        "lesson": None if packet is None else _build_lesson_header(packet),
        "elements": _order_packet_elements(packet) if packet is not None else [],
        "sourceFindings": check["findings"],
        "blocks": _source_blocks(source),
        "draft": _draft_status(source),
        "errors": list(view.errors),
        "state": state_payload,
        "audio": audio_payload,
    }


def build_source_payload(repo_root: Path, lesson_id: str) -> WorkbenchSourcePayload:
    """Return exact editor source and draft state."""
    source = read_workbench_source(repo_root, lesson_id)
    return {"files": source.files, "blocks": _source_blocks(source), "draft": _draft_status(source)}


def build_check_payload(view: AuthorPreview, source: WorkbenchSource) -> WorkbenchCheckResult:
    """Resolve source findings to row notes where their location identifies a source span."""
    notes: list[WorkbenchFinding] = []
    unmatched: list[WorkbenchSourceFinding] = []
    for index, finding in enumerate(view.audit.get("findings", [])):
        block = _finding_block(finding, source) if view.packet is not None else None
        if block is None:
            unmatched.append(cast(WorkbenchSourceFinding, finding))
            continue
        element, separator, block_index = block.key.partition("#")
        notes.append(
            WorkbenchFinding(
                finding_id=f"check-{index}-{finding.get('code', 'source')}",
                target=BlockTarget(element, int(block_index) if separator else None),
                origin="check",
                severity="error"
                if finding.get("severity") in ("blocking", "major", "error", "material")
                else "warning",
                status="open",
                code=finding.get("code", "source"),
                excerpt=finding.get("evidence", ""),
                explanation=finding.get("explanation", ""),
                label=finding_label(str(finding.get("code", "source"))),
                message=str(finding.get("explanation", "")),
                details=json.dumps(finding, ensure_ascii=False, sort_keys=True, default=str),
            )
        )
    return {"findings": unmatched, "errors": list(view.errors), "notes": notes}


def serialize_json(value: object) -> object:
    """Convert dataclasses and containers to JSON values with camelCase keys."""
    if isinstance(value, BaseModel):
        return {_convert_to_camel_case(name): serialize_json(getattr(value, name)) for name in type(value).model_fields}
    if is_dataclass(value) and not isinstance(value, type):
        return {_convert_to_camel_case(item.name): serialize_json(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: serialize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_json(item) for item in value]
    return value


def _project_plan_source(source: str) -> _PlanSourceProjection:
    try:
        frontmatter, body = parse_source_frontmatter(source)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        return _PlanSourceProjection(frontmatter={}, body="", parse_error=str(exc))
    return _PlanSourceProjection(
        frontmatter=cast(dict[str, object], _json_compatible(frontmatter)),
        body=body.strip(),
        parse_error=None,
    )


def _json_compatible(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return str(value)


def _build_audio_payload(audio: dict[str, object]) -> WorkbenchAudioStatus:
    """Project captured author audio state in the lesson transport shape."""
    return {
        "clips": TypeAdapter(list[WorkbenchAudioClipStatus]).validate_python(
            [_camelize_record(item) for item in cast(list[object], audio["clips"])]
        ),
        "voices": TypeAdapter(list[WorkbenchAudioVoice]).validate_python(
            [_camelize_record(item) for item in cast(list[object], audio["voices"])]
        ),
        "missingCount": cast(int, audio["missing_count"]),
        "staleCount": cast(int, audio["stale_count"]),
        "readyCount": cast(int, audio["ready_count"]),
        "blockers": cast(list[str], audio["blockers"]),
    }


def _order_packet_elements(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Project packet content into authored order for the workbench API."""
    sections_by_id = {item.get("id"): item for item in packet.get("sections", [])}
    exercises_by_id = {item.get("id"): item for item in packet.get("exercises", [])}
    elements: list[dict[str, Any]] = []
    for content_item in packet.get("content", []):
        source = sections_by_id if content_item.get("kind") == "section" else exercises_by_id
        element = source.get(content_item.get("id"))
        if isinstance(element, dict):
            prepared = dict(element)
            prepared["anchor_id"] = _make_attribute_token(element.get("id", "section"))
            elements.append(prepared)
    return elements


def _make_attribute_token(value: object) -> str:
    """Normalize an authored identifier for the workbench's block anchor."""
    return str(value).replace(" ", "-")


@lru_cache(maxsize=4)
def _cached_curriculum_payload(
    root_text: str, signature: tuple[tuple[str, int, int, int], ...]
) -> WorkbenchCurriculumPayload:
    return _build_curriculum_payload(Path(root_text))


def _build_curriculum_payload(repo_root: Path) -> WorkbenchCurriculumPayload:
    """Return the ordered curriculum lessons with live authoring status and chat."""
    plan = read_curriculum(repo_root)
    lessons = list_workbench_lessons(repo_root, plan=plan)
    status_chips = {
        lesson.lesson_id: build_curriculum_lesson_status_chips(
            repo_root, lesson.lesson_id, read_workbench_source(repo_root, lesson.lesson_id)
        )
        for lesson in lessons
        if lesson.source != "missing"
    }
    slots = {slot.catalog_id: slot for slot in plan.slots}
    stale = find_stale_curriculum_entries(repo_root, plan)
    findings = check_curriculum(repo_root, plan=plan)
    entries = {
        lesson.lesson_id: _curriculum_entry(
            lesson,
            status_chips.get(lesson.lesson_id),
            slots[lesson.lesson_id],
            lesson.lesson_id in stale,
            findings,
        )
        for lesson in lessons
    }
    return {
        "lessons": cast(list[WorkbenchLesson], [serialize_json(lesson) for lesson in lessons]),
        "entries": entries,
        "findings": cast(list[Any], serialize_json([finding for finding in findings if finding.lesson_id is None])),
        "chat": cast(
            list[StoredChatTurn],
            serialize_json(read_workbench_record(repo_root, "chat", "curriculum", ChatTranscript).turns),
        ),
    }


def _curriculum_entry(
    lesson: WorkbenchLesson,
    chips: tuple[StatusChip, ...] | None,
    slot: CurriculumPlanSlot,
    stale: bool,
    findings: tuple[CurriculumFinding, ...],
) -> WorkbenchCurriculumEntry:
    """Project one curriculum row's status chips, slot outcome and located findings."""
    entry_chips: list[WorkbenchStatusChip] = (
        [{"label": chip.label, "tone": chip.tone} for chip in chips]
        if chips is not None
        else [{"label": "no package", "tone": "neutral"}]
    )
    if stale:
        entry_chips.append({"label": "plan stale", "tone": "warning"})
    return {
        "statusChips": entry_chips,
        "dependsOn": slot.prerequisites,
        "helpfulPrerequisites": slot.helpful_prerequisites,
        "learnerOutcome": slot.learner_outcome,
        "cefrLevel": slot.cefr_level,
        "findings": cast(
            list[Any], serialize_json([finding for finding in findings if finding.lesson_id == lesson.lesson_id])
        ),
    }


def _curriculum_projection_signature(root: Path) -> tuple[tuple[str, int, int, int], ...]:
    """Track source, local review and settings changes without parsing file contents."""
    paths = (
        root / "content" / "curriculum",
        root / "content" / "catalog",
        root / "content" / "lessons",
        root / "drafts",
        root / "store" / "workbench",
        root / ".workbench",
        root / "agents",
        root / "config.yaml",
    )
    candidates: set[Path] = set()
    for path in paths:
        if path.is_dir():
            candidates.add(path)
            candidates.update(item for item in path.rglob("*") if item.is_dir() or item.is_file())
        else:
            candidates.add(path)
    signature = []
    for path in candidates:
        try:
            metadata = path.stat()
        except OSError:
            continue
        if path.is_file() and path.suffix not in {".md", ".yaml", ".yml", ".json"}:
            continue
        signature.append((path.relative_to(root).as_posix(), metadata.st_mtime_ns, metadata.st_size, metadata.st_ino))
    return tuple(sorted(signature))


def _source_blocks(source: WorkbenchSource) -> list[WorkbenchSourceBlock]:
    return [
        {"key": block.key, "kind": block.kind, "source": block.source, "fields": block.fields}
        for block in source.blocks
    ]


def _draft_status(source: WorkbenchSource) -> WorkbenchDraftStatus:
    return {
        "exists": source.exists,
        "startedFrom": source.started_from,
        "dirty": source.dirty,
        "lastCommit": source.last_commit,
    }


def _finding_block(finding: dict[str, Any], source: WorkbenchSource) -> SourceBlock | None:
    location = str(finding.get("location", ""))
    artifact = finding.get("artifact")
    if artifact == "exercises.yaml":
        return _exercise_finding_block(location, source)
    if artifact == "lesson.md":
        return _lesson_finding_block(location, source)
    return None


def _exercise_finding_block(location: str, source: WorkbenchSource) -> SourceBlock | None:
    blocks = [block for block in source.blocks if block.kind == "exercise"]
    for block in blocks:
        if location == block.key or re.search(
            r"^\s*-\s*handle:\s*" + re.escape(location) + r"\s*$", block.source, re.MULTILINE
        ):
            return block
    match = re.match(r"item\[(\d+)\]", location)
    if match and int(match.group(1)) < len(blocks):
        return blocks[int(match.group(1))]
    return None


def _lesson_finding_block(location: str, source: WorkbenchSource) -> SourceBlock | None:
    match = re.match(r"line (\d+)", location)
    if match is None:
        return None
    offset = sum(len(line) for line in source.files["lesson.md"].splitlines(keepends=True)[: int(match.group(1)) - 1])
    return next(
        (block for block in source.blocks if block.kind != "exercise" and block.start <= offset < block.end),
        None,
    )


def _build_lesson_header(packet: dict[str, Any]) -> WorkbenchLessonHeader:
    """Return the lesson-level fields the header shows."""
    return {
        "title": packet.get("title"),
        "cefrLevel": packet.get("cefr_level"),
        "kind": packet.get("kind"),
        "goal": packet.get("goal"),
    }


def _convert_to_camel_case(name: str) -> str:
    """Turn a snake_case field name into camelCase."""
    return K_WEB_CAMEL_BOUNDARY.sub(lambda match: match.group(1).upper(), name)


def _camelize_record(value: object) -> dict[str, object]:
    """Map one application audio record to the workbench's camelCase API shape."""
    if not isinstance(value, dict):
        return {}
    return {_convert_to_camel_case(str(key)): item for key, item in value.items()}


__all__ = ["build_curriculum_checks", "build_curriculum_payload", "build_lesson_payload", "serialize_json"]
