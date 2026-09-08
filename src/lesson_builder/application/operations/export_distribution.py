"""Entry points: `export_distribution`, `load_lesson_package`, and `create_lesson_packet_from_source`.

This application operation composes domain distribution projection with artifact
validation, audio synthesis, staging, and one atomic replacement of ``dist/``.
Artifact checks belong to the validation operation; the domain distribution service
owns packet and catalog projection. This module owns the cross-domain execution and
side effects.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any

from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.load_lesson import load_lesson
from lesson_builder.application.operations.load_lesson import load_plan_metadata
from lesson_builder.application.operations.synthesize_audio import AudioSettings
from lesson_builder.application.operations.synthesize_audio import AudioSynthesisClient
from lesson_builder.application.operations.synthesize_audio import synthesize_lesson_audio
from lesson_builder.application.operations.synthesize_audio import validate_lesson_audio_sources
from lesson_builder.application.operations.validate_distribution import load_planned_lesson_ids
from lesson_builder.application.operations.validate_distribution import validate_distribution
from lesson_builder.application.operations.validate_distribution import validate_lesson_inventory
from lesson_builder.application.operations.write_export_schema import write_export_json_schema
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.distribution.settings import K_CATALOG_FILENAME
from lesson_builder.domain.distribution.settings import K_DISTRIBUTION_STAGING_PREFIX
from lesson_builder.domain.lesson.models import Lesson
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.workspace.paths import WorkspacePaths


def export_distribution(
    repo_root: Path,
    *,
    output_root: Path | None = None,
    audio_settings: AudioSettings | None = None,
    audio_cache_root: Path | None = None,
    synthesis_client: AudioSynthesisClient | None = None,
    audio_workers: int = 1,
    audio_public_base_url: str | None = None,
) -> dict[str, object]:
    """Export the complete lesson distribution for ``repo_root``.

    The authoritative inventory is the committed ``content/curriculum/plan.yaml`` slot
    order. Every planned lesson must have a complete ``content/lessons/<id>/`` package,
    no complete package may exist outside the plan, and an incomplete package
    directory is an error rather than a skip. All packages are compiled and
    projected before anything is published; the catalog and JSON schema are
    emitted into a staging distribution, the staged distribution is validated with
    ``validate_distribution``, and only then is ``dist/`` replaced atomically. With
    ``output_root`` the distribution is published below that root while sources are
    still read from ``repo_root``.
    """
    repo_root = Path(repo_root)
    layout = WorkspacePaths(repo_root)
    distribution_root = Path(output_root) if output_root is not None else repo_root
    distribution_root.mkdir(parents=True, exist_ok=True)
    _reconcile_dist_recovery(WorkspacePaths(distribution_root).dist_root)
    lesson_ids = load_planned_lesson_ids(repo_root)
    if audio_workers < 1:
        raise ValueError("audio_workers must be at least 1")
    if synthesis_client is not None and audio_workers != 1:
        raise ValueError("an injected synthesis client requires audio_workers=1")
    resolved_public_base_url = audio_public_base_url or (
        audio_settings.public_base_url if audio_settings is not None else None
    )
    if audio_settings is not None and not resolved_public_base_url:
        raise ValueError("audio-enabled distribution requires audio_public_base_url")
    validate_lesson_inventory(repo_root, lesson_ids)
    character_registry = load_optional_character_registry(repo_root, search_parents=False)
    exports, compiled_packages = _prepare_distribution_exports(
        repo_root=repo_root,
        layout=layout,
        distribution_root=distribution_root,
        lesson_ids=lesson_ids,
        audio_settings=audio_settings,
        character_registry=character_registry,
    )
    current_dist = WorkspacePaths(distribution_root).dist_root
    staging_root = distribution_root / f"{K_DISTRIBUTION_STAGING_PREFIX}{uuid.uuid4().hex}"
    staged_dist = WorkspacePaths(staging_root).dist_root
    try:
        staged_lessons_dir = staged_dist / "lessons"
        staged_lessons_dir.mkdir(parents=True)
        if audio_settings is not None:
            exports = _exports_with_audio(
                lesson_ids=lesson_ids,
                compiled_packages=compiled_packages,
                staging_root=staging_root,
                settings=audio_settings,
                cache_root=audio_cache_root or repo_root / "store" / "cache" / "audio",
                synthesis_client=synthesis_client,
                workers=audio_workers,
                public_base_url=str(resolved_public_base_url),
                character_registry=character_registry or audio_settings.character_registry,
            )
        created, unchanged, updated = _write_staged_packets(
            exports=exports,
            staged_lessons_dir=staged_lessons_dir,
            current_dist=current_dist,
        )
        catalog_text = _json_text(DistributionService().create_distribution_catalog(exports))
        (staged_dist / K_CATALOG_FILENAME).write_text(catalog_text, encoding="utf-8")
        write_export_json_schema(staged_dist / "schema" / "lesson.schema.json")
        validation = validate_distribution(staging_root, expected_lesson_ids=lesson_ids)
        catalog_key = "catalog_updated"
        current_catalog = current_dist / K_CATALOG_FILENAME
        if current_catalog.is_file() and current_catalog.read_text(encoding="utf-8") == catalog_text:
            catalog_key = "catalog_unchanged"
        _replace_dist_atomically(staged_dist=staged_dist, current_dist=current_dist)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    return {
        "created": created,
        "unchanged": unchanged,
        "updated": updated,
        catalog_key: 1,
        "schema_path": str(current_dist / "schema" / "lesson.schema.json"),
        "lesson_count": len(exports),
        "lesson_ids": lesson_ids,
        "distribution_root": str(distribution_root),
        "validation": validation,
        "audio_mode": audio_settings is not None,
        "audio_workers": audio_workers if audio_settings is not None else 0,
    }


def load_lesson_package(source_dir: Path) -> tuple[Lesson, dict[str, Any]]:
    """Load one authored package for an application operation."""
    package_dir = Path(source_dir)
    return load_lesson(package_dir), load_plan_metadata(package_dir / "plan.md")


def create_lesson_packet_from_source(
    source_dir: Path,
    *,
    language: str = "nb-NO",
    audio: list[dict[str, Any]] | None = None,
    audio_bindings: Mapping[str, str] | None = None,
    character_registry: CharacterRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one authored package and project it into a distribution packet."""
    package_dir = Path(source_dir)
    registry = character_registry or load_optional_character_registry(package_dir)
    lesson, plan_metadata = load_lesson_package(package_dir)
    kind = plan_metadata.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("lesson package plan.md must contain a non-empty kind")
    packet = DistributionService().create_lesson_packet(
        lesson,
        kind=kind,
        language=language,
        audio=audio,
        audio_bindings=audio_bindings,
        character_registry=registry,
        allow_unregistered_characters=registry is None,
    )
    return packet, plan_metadata


def _prepare_distribution_exports(
    *,
    repo_root: Path,
    layout: WorkspacePaths,
    distribution_root: Path,
    lesson_ids: list[str],
    audio_settings: AudioSettings | None,
    character_registry: CharacterRegistry | None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[tuple[Lesson, dict[str, Any]]]]:
    """Load or project all planned lessons before staging the distribution."""
    if audio_settings is None:
        _require_provider_free_target(distribution_root)
        if character_registry is None:
            exports = [create_lesson_packet_from_source(layout.lessons_root / lesson_id) for lesson_id in lesson_ids]
        else:
            exports = [
                create_lesson_packet_from_source(
                    layout.lessons_root / lesson_id,
                    character_registry=character_registry,
                )
                for lesson_id in lesson_ids
            ]
        _require_matching_packet_ids(lesson_ids, exports)
        return exports, []

    compiled_packages = [load_lesson_package(layout.lessons_root / lesson_id) for lesson_id in lesson_ids]
    _require_matching_compiled_ids(lesson_ids, compiled_packages)
    return [], compiled_packages


def _write_staged_packets(
    *,
    exports: list[tuple[dict[str, Any], dict[str, Any]]],
    staged_lessons_dir: Path,
    current_dist: Path,
) -> tuple[int, int, int]:
    """Write packet JSON and count created, unchanged, and updated packets."""
    created = unchanged = updated = 0
    for export, _metadata in exports:
        packet_id = str(export["id"])
        packet_text = _json_text(export)
        (staged_lessons_dir / f"{packet_id}.json").write_text(packet_text, encoding="utf-8")
        current_packet = current_dist / "lessons" / f"{packet_id}.json"
        if not current_packet.is_file():
            created += 1
        elif current_packet.read_text(encoding="utf-8") == packet_text:
            unchanged += 1
        else:
            updated += 1
    return created, unchanged, updated


def _replace_dist_atomically(*, staged_dist: Path, current_dist: Path) -> None:
    """Publish a staged ``dist/`` with one rename, restoring the old distribution on error.

    Called only after the complete staged distribution has passed validation.
    """
    backup_dist = current_dist.with_name(f".{current_dist.name}.backup")
    if backup_dist.exists():
        raise RuntimeError(f"ambiguous interrupted dist replacement: {backup_dist}")
    moved_current = False
    if current_dist.exists():
        current_dist.rename(backup_dist)
        moved_current = True
    try:
        staged_dist.rename(current_dist)
    except Exception:
        if moved_current and backup_dist.exists() and not current_dist.exists():
            backup_dist.rename(current_dist)
        raise
    if backup_dist.exists():
        with suppress(OSError):
            shutil.rmtree(backup_dist)


def _reconcile_dist_recovery(current_dist: Path) -> None:
    """Recover the one recognized interrupted dist replacement state."""
    backup_dist = current_dist.with_name(f".{current_dist.name}.backup")
    siblings = list(current_dist.parent.glob(f".{current_dist.name}.backup-*"))
    if siblings:
        raise RuntimeError("unrecognized interrupted dist backup state: " + ", ".join(map(str, siblings)))
    if backup_dist.exists() and not current_dist.exists():
        backup_dist.rename(current_dist)
    elif backup_dist.exists() and current_dist.exists():
        try:
            validate_distribution(current_dist.parent)
        except Exception as exc:
            raise RuntimeError("current dist is invalid while deterministic backup exists") from exc
        shutil.rmtree(backup_dist)


def _exports_with_audio(
    *,
    lesson_ids: list[str],
    compiled_packages: list[tuple[Lesson, dict[str, Any]]],
    staging_root: Path,
    settings: AudioSettings,
    cache_root: Path,
    synthesis_client: AudioSynthesisClient | None,
    workers: int,
    public_base_url: str,
    character_registry: CharacterRegistry,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Materialize cached real audio and project every compiled package."""
    from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks

    work = list(zip(lesson_ids, compiled_packages, strict=True))
    prepared: list[tuple[str, tuple[Lesson, dict[str, Any]], list[dict[str, Any]]]] = []
    for lesson_id, package in work:
        lesson, _metadata = package
        transcript_blocks = derive_transcript_blocks(lesson.model_dump(mode="json"))
        candidates = [
            block
            for block in transcript_blocks
            if block.get("origin") in {"lesson_reading", "lesson_example", "exercise_target"}
        ]
        validate_lesson_audio_sources(candidates, settings)
        prepared.append((lesson_id, package, candidates))

    def project(
        item: tuple[
            str,
            tuple[Lesson, dict[str, Any]],
            list[dict[str, Any]],
        ],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        lesson_id, (lesson, metadata), candidates = item
        audio, bindings = synthesize_lesson_audio(
            lesson_id=lesson_id,
            transcript_blocks=candidates,
            distribution_root=staging_root,
            settings=settings,
            synthesis_client=synthesis_client,
            cache_root=cache_root,
        )
        _attach_public_audio_urls(audio, public_base_url=public_base_url)
        kind = metadata.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"lessons/{lesson_id}/plan.md must contain a non-empty kind")
        export = DistributionService().create_lesson_packet(
            lesson,
            kind=kind,
            audio=audio,
            audio_bindings=bindings,
            character_registry=character_registry,
        )
        return export, metadata

    if workers == 1:
        return [project(item) for item in prepared]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(project, prepared))


def _attach_public_audio_urls(audio: list[dict[str, Any]], *, public_base_url: str) -> None:
    """Derive ready-to-use URLs from stable distribution-relative object keys."""
    base = public_base_url.rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("audio_public_base_url must be an absolute HTTPS URL")
    for entry in audio:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("synthesized audio entry requires a path")
        entry["url"] = f"{base}/{path}"


def _require_matching_compiled_ids(
    lesson_ids: list[str],
    compiled_packages: list[tuple[Lesson, dict[str, Any]]],
) -> None:
    """Fail when compiled lesson identity disagrees with its curriculum slot."""
    for lesson_id, (lesson, _metadata) in zip(lesson_ids, compiled_packages, strict=True):
        if lesson.key != lesson_id:
            raise ValueError(
                f"lessons/{lesson_id} compiled to packet id {lesson.key!r}; "
                "source directory and packet identity must agree"
            )


def _require_matching_packet_ids(lesson_ids: list[str], exports: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    """Fail closed when a compiled packet identity disagrees with the plan."""
    for lesson_id, (export, _metadata) in zip(lesson_ids, exports, strict=True):
        packet_id = str(export["id"])
        if packet_id != lesson_id:
            raise ValueError(
                f"lessons/{lesson_id} compiled to packet id {packet_id!r}; "
                "source directory and packet identity must agree"
            )


def _require_provider_free_target(distribution_root: Path) -> None:
    """Refuse to erase a real-audio distribution with provider-free export."""
    if _has_distribution_audio(WorkspacePaths(distribution_root).dist_root):
        raise ValueError(
            "provider-free distribution export cannot overwrite a distribution containing audio; "
            "use export for an approved audio distribution"
        )


def _has_distribution_audio(dist_root: Path) -> bool:
    """Detect real audio that provider-free export must not erase."""
    lessons_dir = dist_root / "lessons"
    if not lessons_dir.is_dir():
        return False
    for packet_file in lessons_dir.glob("*.json"):
        try:
            packet = json.loads(packet_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        media = packet.get("media") if isinstance(packet, dict) else None
        audio = media.get("audio") if isinstance(media, dict) else None
        if isinstance(audio, list) and audio:
            return True
    return False


def _json_text(payload: object) -> str:
    """Serialize one distribution JSON document in the canonical on-disk form."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "export_distribution",
    "load_lesson_package",
    "create_lesson_packet_from_source",
]
