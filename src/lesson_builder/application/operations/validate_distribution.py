"""Entry points: `validate_distribution`, `validate_committed_distribution`, `load_planned_lesson_ids`, `validate_lesson_inventory`, and `validate_audio_asset`.

This operation owns artifact-level checks: the curriculum inventory, packet and
catalog shape, public schema, audio references, and parity with canonical lesson
source. It does not publish a distribution; export and release operations call it
before performing their own side effects.
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import Any

import yaml

from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.load_lesson import load_lesson_source
from lesson_builder.application.operations.load_lesson import load_plan_metadata
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.distribution.settings import K_AUDIO_PATH_PREFIX
from lesson_builder.domain.distribution.settings import K_CATALOG_FILENAME
from lesson_builder.domain.distribution.settings import K_PACKET_PATH_PREFIX
from lesson_builder.domain.distribution.settings import K_REQUIRED_PACKAGE_FILES
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.models.export import ExportedAudio
from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.domain.lesson.settings import K_LESSON_SCHEMA_VERSION
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.workspace.paths import WorkspacePaths


def validate_audio_asset(path: Path) -> int:
    """Validate a distribution/cache WAV asset and return its rounded duration."""
    try:
        with wave.open(str(path), "rb") as handle:
            frame_rate = handle.getframerate()
            frame_count = handle.getnframes()
    except (OSError, wave.Error) as exc:
        raise ValueError(f"audio asset is not a readable WAV file: {path}") from exc
    if frame_rate <= 0 or frame_count <= 0:
        raise ValueError(f"audio asset is empty: {path}")
    return int(round(frame_count * 1000 / frame_rate))


def validate_distribution(
    distribution_root: Path, *, expected_lesson_ids: list[str] | None = None
) -> dict[str, object]:
    """Validate one complete lesson distribution in place and return a summary.

    The distribution root is the directory that contains ``dist/``. Packets are
    discovered directly from ``dist/lessons`` and independently validated. The
    required catalog's contiguous ``position`` values are the curriculum order.
    Validation fails closed on duplicate lesson IDs, catalog or packet-set drift,
    invalid lesson packets, and audio files that are missing, unreadable, or stale
    against their optional SHA-256.
    """
    root = Path(distribution_root)
    dist = WorkspacePaths(root).dist_root
    entries = _discover_packet_entries(dist)
    if not entries:
        raise ValueError(f"no lesson packets found at {dist / 'lessons'}")

    packet_ids = [str(entry["id"]) for entry in entries]
    catalog_order = _validate_catalog(dist / K_CATALOG_FILENAME, packet_ids=packet_ids)
    _validate_expected_lesson_ids(packet_ids, catalog_order, expected_lesson_ids)
    lesson_ids = catalog_order
    audio_file_count, audio_total_bytes, referenced_audio_paths = _validate_packet_entries(
        root=root,
        dist=dist,
        entries=entries,
    )
    actual_audio_paths = _discover_audio_paths(dist)
    stale_audio = sorted(actual_audio_paths - referenced_audio_paths)
    if stale_audio:
        raise ValueError(f"distribution contains unreferenced audio: {', '.join(stale_audio)}")
    schema_file = _validate_distribution_schema(dist)
    return {
        "distribution_root": str(root),
        "schema_version": K_LESSON_SCHEMA_VERSION,
        "lesson_ids": lesson_ids,
        "lesson_count": len(lesson_ids),
        "packet_count": len(entries),
        "catalog_present": True,
        "audio_file_count": audio_file_count,
        "audio_total_bytes": audio_total_bytes,
        "schema_path": str(schema_file),
    }


def validate_committed_distribution(repo_root: Path, *, distribution_root: Path | None = None) -> dict[str, object]:
    """Validate a distribution and require its non-audio content to match source."""
    root = Path(repo_root)
    published_root = Path(distribution_root) if distribution_root is not None else root
    lesson_ids = load_planned_lesson_ids(root)
    validate_lesson_inventory(root, lesson_ids)
    summary = validate_distribution(published_root, expected_lesson_ids=lesson_ids)
    character_registry = load_optional_character_registry(root, search_parents=False)
    for lesson_id in lesson_ids:
        expected = _create_expected_packet(
            WorkspacePaths(root).lessons_root / lesson_id,
            character_registry=character_registry,
        )
        actual_path = WorkspacePaths(published_root).dist_root / "lessons" / f"{lesson_id}.json"
        actual = _read_json_payload(actual_path, f"lesson packet {lesson_id}")
        if _strip_audio(actual) != expected:
            raise ValueError(f"distribution packet {lesson_id!r} is stale against canonical source")
    return summary


def load_planned_lesson_ids(repo_root: Path) -> list[str]:
    """Read the committed curriculum lesson order from ``content/curriculum/plan.yaml``."""
    plan_path = WorkspacePaths(repo_root).curriculum_plan
    if not plan_path.is_file():
        raise ValueError(f"curriculum plan not found at {plan_path}")
    try:
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read curriculum plan {plan_path}: {exc}") from exc
    if not isinstance(plan, dict):
        raise TypeError("content/curriculum/plan.yaml must be a mapping")
    slots = plan.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("content/curriculum/plan.yaml must declare a non-empty slots list")
    lesson_ids: list[str] = []
    seen_ids: set[str] = set()
    for slot in slots:
        lesson_id = slot.get("catalog_id") if isinstance(slot, dict) else None
        if not isinstance(lesson_id, str) or not lesson_id:
            raise ValueError("every content/curriculum/plan.yaml slot must declare a catalog_id")
        try:
            validate_slug(lesson_id)
        except ValueError as exc:
            raise ValueError(
                f"content/curriculum/plan.yaml catalog_id {lesson_id!r} is not a valid lesson slug"
            ) from exc
        if lesson_id in seen_ids:
            raise ValueError(f"content/curriculum/plan.yaml repeats lesson id {lesson_id!r}")
        seen_ids.add(lesson_id)
        lesson_ids.append(lesson_id)
    return lesson_ids


def validate_lesson_inventory(repo_root: Path, lesson_ids: list[str]) -> None:
    """Fail closed unless complete ``lessons/`` packages exactly match the plan."""
    lessons_root = WorkspacePaths(repo_root).lessons_root
    if not lessons_root.is_dir():
        raise ValueError(f"lessons source directory not found at {lessons_root}")
    package_ids = _collect_complete_lesson_package_ids(lessons_root)
    planned_ids = set(lesson_ids)
    missing_packages = [lesson_id for lesson_id in lesson_ids if lesson_id not in package_ids]
    extra_packages = sorted(package_ids - planned_ids)
    if missing_packages or extra_packages:
        raise ValueError(
            "distribution inventory does not match content/curriculum/plan.yaml ("
            + "; ".join(_build_inventory_problems(missing_packages, extra_packages))
            + ")"
        )


def _create_expected_packet(
    source_dir: Path,
    *,
    character_registry: CharacterRegistry | None,
) -> dict[str, Any]:
    """Load one source package for committed-distribution parity validation."""
    package_dir = Path(source_dir)
    lesson = load_lesson_source(
        (package_dir / "lesson.md").read_text(encoding="utf-8"),
        (package_dir / "exercises.yaml").read_text(encoding="utf-8"),
    )
    metadata = load_plan_metadata(package_dir / "plan.md")
    kind = metadata.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("lesson package plan.md must contain a non-empty kind")
    return DistributionService().create_lesson_packet(
        lesson,
        kind=kind,
        character_registry=character_registry,
        allow_unregistered_characters=character_registry is None,
    )


def _validate_expected_lesson_ids(
    packet_ids: list[str],
    catalog_order: list[str],
    expected_lesson_ids: list[str] | None,
) -> None:
    """Require packet inventory and catalog order to match an optional expectation."""
    if expected_lesson_ids is None:
        return
    expected_ids = list(expected_lesson_ids)
    if set(packet_ids) != set(expected_ids) or len(packet_ids) != len(expected_ids):
        raise ValueError(
            "distribution lesson inventory does not match the expected curriculum inventory: "
            f"{sorted(packet_ids)!r} != {sorted(expected_ids)!r}"
        )
    if catalog_order != expected_ids:
        raise ValueError(
            "distribution catalog lesson order does not match the expected curriculum order: "
            f"{catalog_order!r} != {expected_ids!r}"
        )


def _validate_packet_entries(
    *,
    root: Path,
    dist: Path,
    entries: list[dict[str, Any]],
) -> tuple[int, int, set[str]]:
    """Validate packets and their referenced audio, returning audio totals."""
    audio_file_count = 0
    audio_total_bytes = 0
    referenced_audio_paths: set[str] = set()
    for entry in entries:
        packet = _load_packet(root, str(entry["path"]))
        _require_packet_matches_path(packet, entry)
        for audio in packet.media.audio:
            _validate_packet_audio(packet, dist, audio)
            audio_bytes = (dist / audio.path).read_bytes()
            audio_file_count += 1
            audio_total_bytes += len(audio_bytes)
            referenced_audio_paths.add(audio.path)
    return audio_file_count, audio_total_bytes, referenced_audio_paths


def _validate_packet_audio(packet: ExportedLesson, dist: Path, audio: ExportedAudio) -> None:
    """Validate one packet audio reference and its optional metadata."""
    _require_safe_audio_path(audio.path)
    audio_file = dist / audio.path
    if not audio_file.is_file():
        raise ValueError(f"packet {packet.id!r} references missing audio {audio.path!r}")
    duration_ms = validate_audio_asset(audio_file)
    if audio.duration_ms is not None and duration_ms != audio.duration_ms:
        raise ValueError(f"packet {packet.id!r} audio {audio.path!r} does not match its duration_ms")
    audio_bytes = audio_file.read_bytes()
    if audio.sha256 is not None and hashlib.sha256(audio_bytes).hexdigest() != audio.sha256:
        raise ValueError(f"packet {packet.id!r} audio {audio.path!r} does not match its sha256")


def _discover_audio_paths(dist: Path) -> set[str]:
    """Return all published lesson audio paths."""
    audio_root = dist / "audio" / "lessons"
    if not audio_root.is_dir():
        return set()
    return {path.relative_to(dist).as_posix() for path in audio_root.rglob("*.wav") if path.is_file()}


def _validate_distribution_schema(dist: Path) -> Path:
    """Validate the generated distribution schema and return its path."""
    schema_file = dist / "schema" / "lesson.schema.json"
    if not schema_file.is_file():
        raise ValueError(f"distribution schema not found at {schema_file}")
    schema = _read_json_payload(schema_file, "distribution schema")
    if schema != ExportedLesson.model_json_schema():
        raise ValueError("distribution schema does not match the current public export schema")
    return schema_file


def _discover_packet_entries(dist: Path) -> list[dict[str, Any]]:
    """Discover packet paths without using filesystem order as curriculum order."""
    lessons_dir = dist / "lessons"
    if not lessons_dir.is_dir():
        return []
    return [
        {
            "id": packet_file.stem,
            "path": f"{K_PACKET_PATH_PREFIX}{packet_file.name}",
        }
        for packet_file in sorted(lessons_dir.glob("*.json"), key=lambda path: path.name)
        if packet_file.is_file()
    ]


def _validate_catalog(path: Path, *, packet_ids: list[str]) -> list[str]:
    """Validate required catalog metadata and return its explicit curriculum order."""
    if not path.is_file():
        raise ValueError(f"distribution catalog not found at {path}")
    raw_catalog = _read_json_payload(path, "distribution catalog")
    if not isinstance(raw_catalog, dict):
        raise TypeError("distribution catalog must be a JSON object")
    unexpected_catalog_fields = set(raw_catalog).difference({"lessons"})
    if unexpected_catalog_fields:
        raise ValueError(
            "distribution catalog contains packet or factory metadata: " + ", ".join(sorted(unexpected_catalog_fields))
        )
    raw_entries = raw_catalog.get("lessons")
    if not isinstance(raw_entries, list):
        raise TypeError("distribution catalog lessons must be a list")
    allowed_fields = {"lesson_id", "position", "family_id", "published", "active"}
    entries: list[tuple[int, str]] = []
    seen_ids: set[str] = set()
    seen_positions: set[int] = set()
    for raw_entry in raw_entries:
        position, lesson_id = _validate_catalog_entry(raw_entry, allowed_fields)
        if lesson_id in seen_ids:
            raise ValueError(f"distribution catalog repeats lesson id {lesson_id!r}")
        if position in seen_positions:
            raise ValueError(f"distribution catalog repeats position {position}")
        seen_ids.add(lesson_id)
        seen_positions.add(position)
        entries.append((position, lesson_id))
    if sorted(seen_positions) != list(range(len(entries))):
        raise ValueError("distribution catalog positions must be contiguous starting at zero")
    if seen_ids != set(packet_ids):
        raise ValueError(
            f"distribution catalog lesson inventory does not match packets: {sorted(seen_ids)!r} != {sorted(packet_ids)!r}"
        )
    return [lesson_id for _position, lesson_id in sorted(entries)]


def _validate_catalog_entry(raw_entry: object, allowed_fields: set[str]) -> tuple[int, str]:
    """Validate one catalog entry and return its position and lesson ID."""
    if not isinstance(raw_entry, dict):
        raise TypeError("distribution catalog lesson entries must be JSON objects")
    unexpected = set(raw_entry).difference(allowed_fields)
    if unexpected:
        raise ValueError("distribution catalog contains packet or factory metadata: " + ", ".join(sorted(unexpected)))
    lesson_id = raw_entry.get("lesson_id")
    position = raw_entry.get("position")
    if not isinstance(lesson_id, str) or not lesson_id:
        raise ValueError("distribution catalog lesson entries must declare a non-empty lesson_id")
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise ValueError(f"distribution catalog position for {lesson_id!r} must be a non-negative integer")
    family_id = raw_entry.get("family_id")
    if family_id is not None and (not isinstance(family_id, str) or not family_id):
        raise ValueError(f"distribution catalog family_id for {lesson_id!r} must be a non-empty string")
    for status_field in ("published", "active"):
        status = raw_entry.get(status_field)
        if status is not None and not isinstance(status, bool):
            raise ValueError(f"distribution catalog {status_field} for {lesson_id!r} must be a boolean")
    return position, lesson_id


def _strip_audio(payload: object) -> object:
    """Return a JSON value with permitted distribution-audio differences removed."""
    if isinstance(payload, list):
        return [_strip_audio(value) for value in payload]
    if not isinstance(payload, dict):
        return payload
    normalized = {key: _strip_audio(value) for key, value in payload.items() if key != "audio_id"}
    media = normalized.get("media")
    if isinstance(media, dict):
        media["audio"] = []
    return normalized


def _read_json_payload(path: Path, label: str) -> object:
    """Read and parse one distribution JSON document with a named failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} at {path}: {exc}") from exc


def _load_packet(distribution_root: Path, packet_path: str) -> ExportedLesson:
    """Load one distribution packet and validate it against the public export schema."""
    packet_file = distribution_root / packet_path
    try:
        return ExportedLesson.model_validate_json(packet_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"packet {packet_path!r} does not satisfy the public export schema") from exc


def _require_packet_matches_path(packet: ExportedLesson, entry: dict[str, Any]) -> None:
    """Require a discovered packet's identity to agree with its filename."""
    entry_id = str(entry["id"])
    entry_path = str(entry["path"])
    if packet.id != entry_id:
        raise ValueError(f"packet filename id {entry_id!r} does not match packet id {packet.id!r}")
    expected_path = f"{K_PACKET_PATH_PREFIX}{entry_id}.json"
    if entry_path != expected_path:
        raise ValueError(f"packet path {entry_path!r} does not match lesson id {entry_id!r}")


def _require_safe_audio_path(path: str) -> None:
    """Reject packet audio paths that could escape the distribution."""
    posix_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    if (
        not path.startswith(K_AUDIO_PATH_PREFIX)
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise ValueError(f"packet audio path is unsafe: {path!r}")


def _collect_complete_lesson_package_ids(lessons_root: Path) -> set[str]:
    """Return source package IDs after rejecting incomplete directories."""
    package_ids: set[str] = set()
    for package_dir in sorted(lessons_root.iterdir()):
        if not package_dir.is_dir():
            continue
        missing_files = [name for name in K_REQUIRED_PACKAGE_FILES if not (package_dir / name).is_file()]
        if missing_files:
            raise ValueError(f"incomplete lesson package {package_dir}: missing {', '.join(missing_files)}")
        package_ids.add(package_dir.name)
    return package_ids


def _build_inventory_problems(missing_packages: list[str], extra_packages: list[str]) -> list[str]:
    """Format planned and unplanned package differences for one diagnostic."""
    problems: list[str] = []
    if missing_packages:
        problems.append("planned lessons without a complete source package: " + ", ".join(missing_packages))
    if extra_packages:
        problems.append(
            "complete source packages absent from content/curriculum/plan.yaml: " + ", ".join(extra_packages)
        )
    return problems


__all__ = [
    "load_planned_lesson_ids",
    "validate_committed_distribution",
    "validate_lesson_inventory",
    "validate_distribution",
    "validate_audio_asset",
]
