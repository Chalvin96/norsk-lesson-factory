"""Entry points: `run_release` (local CLI) and `verify_release` (tag CI job).

Planning is deterministic and provider-free. Local approval resumes the
source-bound plan, seeds the shared synthesis cache from discovered historical
audio, exports only missing recordings, and publishes audio before the GitHub
archive. Tag CI never synthesizes: `verify_release` downloads the already
published archive and proves it matches canonical source.

Result payloads stay bounded; the full candidate detail lives in the persisted
release plan file, not in command output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import cast

from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.export_distribution import load_lesson_package
from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.package_distribution import package_distribution
from lesson_builder.application.operations.publish_release import publish_release
from lesson_builder.application.operations.synthesize_audio import AudioCandidateIdentity
from lesson_builder.application.operations.synthesize_audio import AudioSettings
from lesson_builder.application.operations.synthesize_audio import AudioSynthesisClient
from lesson_builder.application.operations.synthesize_audio import load_audio_settings
from lesson_builder.application.operations.synthesize_audio import plan_lesson_audio_candidates
from lesson_builder.application.operations.validate_distribution import load_planned_lesson_ids
from lesson_builder.application.operations.validate_distribution import validate_audio_asset
from lesson_builder.application.operations.validate_distribution import validate_committed_distribution
from lesson_builder.clients.github.releases import download_release_asset
from lesson_builder.clients.github.releases import resolve_release_tag_commit
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.workspace.paths import WorkspacePaths

K_RELEASE_PLAN_VERSION = 2
K_RELEASE_PLAN_FILENAME = "release-plan.json"
K_RELEASE_ARCHIVE_FILENAME = "lessons.tar.gz"
K_RELEASE_ARCHIVE_DIST_PREFIX = "dist/"
K_RELEASE_AUDIO_COST_PER_MILLION_CHARS = 30.0
K_RELEASE_TAG_PATTERN = re.compile(r"v\d{4}\.\d{2}\.\d{2}")
K_RELEASE_GIT_TIMEOUT_SECONDS = 30


def run_release(
    *,
    repo_root: Path,
    approve: bool = False,
    tag: str | None = None,
    github_repository: str | None = None,
    service_account_path: Path | None = None,
    max_cost: float | None = None,
    synthesis_client: AudioSynthesisClient | None = None,
) -> dict[str, object]:
    """Plan or execute one source-bound release, with no interactive prompts."""
    root = Path(repo_root)
    if approve:
        plan = _read_existing_plan(root)
        if plan["source_digest"] != _calculate_source_digest(root):
            raise ValueError("release plan is stale against canonical source; rerun `uv run lesson-data release`")
        settings = load_audio_settings(
            repo_root=root,
            service_account_path=service_account_path,
            require_service_account=True,
        )
    else:
        settings = load_audio_settings(
            repo_root=root,
            service_account_path=service_account_path,
            require_service_account=False,
        )
        plan = _load_or_write_plan(
            root,
            settings=settings,
            release_tag=tag or _build_default_tag(),
            github_repository=_find_github_repository(root, github_repository),
        )
    estimated_cost = cast(float, plan["estimated_cost_usd"])
    if max_cost is not None and estimated_cost > max_cost:
        raise ValueError(
            f"estimated synthesis cost ${plan['estimated_cost_usd']:.2f} exceeds --max-cost ${max_cost:.2f}; "
            "lower the missing audio count or raise the cap"
        )
    if not approve:
        return {**plan, "approved": False, "next_command": _build_approval_command(plan)}
    planned_tag = cast(str, plan["release_tag"])
    if tag is not None and tag != planned_tag:
        raise ValueError("release tag differs from the source-bound plan; rerun planning with that tag")
    resolved_tag = planned_tag
    _validate_release_tag(resolved_tag)
    planned_repository = cast(str | None, plan.get("github_repository"))
    repository = _find_github_repository(root, github_repository) or planned_repository
    if not repository:
        raise ValueError(
            "GitHub repository is required for approval; pass --github-repository or set GITHUB_REPOSITORY"
        )
    if planned_repository is not None and repository != planned_repository:
        raise ValueError("GitHub repository differs from the source-bound plan; rerun planning for that repository")
    _require_tag_matches_source(root, resolved_tag, repository=repository)
    cache_root = WorkspacePaths(root).audio_cache_root
    _seed_cache(plan, cache_root=cache_root)
    output_root = WorkspacePaths(root).release_scratch_root / resolved_tag
    if not settings.public_base_url:
        raise ValueError("audio public URL is required for approval; set LESSON_AUDIO_PUBLIC_BASE_URL")
    exported = export_distribution(
        root,
        output_root=output_root,
        audio_settings=settings,
        audio_cache_root=cache_root,
        synthesis_client=synthesis_client,
        audio_public_base_url=settings.public_base_url,
    )
    validation = validate_committed_distribution(root, distribution_root=output_root)
    archive = output_root / K_RELEASE_ARCHIVE_FILENAME
    packaged = package_distribution(
        repo_root=root,
        distribution_root=output_root,
        output_path=archive,
        include_audio=False,
    )
    publication = publish_release(
        archive_path=archive,
        repo_root=root,
        distribution_root=output_root,
        upload_s3=True,
        env_path=root / ".env",
        github_repository=repository,
        github_tag=resolved_tag,
        archive_includes_audio=False,
    )
    return {
        "approved": True,
        "tag": resolved_tag,
        "repository": repository,
        "plan": _build_plan_summary(plan),
        "export": exported,
        "validation": validation,
        "package": packaged,
        "publication": publication,
    }


def verify_release(
    *,
    repo_root: Path,
    tag: str,
    github_repository: str | None = None,
) -> dict[str, object]:
    """Verify one published GitHub release archive against canonical source.

    Tag CI calls this instead of approving: it downloads the release archive,
    proves the archive is an audio-free projection whose packets match the
    tagged source and carry ready HTTPS audio references, and never contacts a
    synthesis provider or object storage.
    """
    root = Path(repo_root)
    repository = github_repository or os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise ValueError(
            "GitHub repository is required for verification; pass --github-repository or set GITHUB_REPOSITORY"
        )
    if not tag:
        raise ValueError("tag is required for verification of one concrete published release")
    _validate_release_tag(tag)
    scratch = WorkspacePaths(root).release_scratch_root / "verify" / tag
    archive_path = scratch / K_RELEASE_ARCHIVE_FILENAME
    extract_root = scratch / "extracted"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        download_release_asset(
            repository=repository,
            tag=tag,
            asset_name=K_RELEASE_ARCHIVE_FILENAME,
            destination=archive_path,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("GitHub release asset download timed out; verification state is unknown") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"GitHub release {tag!r} in {repository} does not expose the {K_RELEASE_ARCHIVE_FILENAME!r} asset; "
            "publish it locally with `uv run lesson-data release --approve` first"
        ) from exc
    if extract_root.exists():
        shutil.rmtree(extract_root)
    _extract_release_archive(archive_path, extract_root)
    validation = validate_committed_distribution(root, distribution_root=extract_root, require_local_audio=False)
    _require_audio_complete_packets(root, WorkspacePaths(extract_root).dist_root)
    audio_reference_count = cast(int, validation["audio_reference_count"])
    return {
        "verified": True,
        "tag": tag,
        "repository": repository,
        "asset_name": K_RELEASE_ARCHIVE_FILENAME,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "archive_size_bytes": archive_path.stat().st_size,
        "lesson_count": validation["lesson_count"],
        "audio_reference_count": audio_reference_count,
        "distribution_root": str(extract_root),
    }


def _load_or_write_plan(
    root: Path, *, settings: AudioSettings, release_tag: str, github_repository: str | None
) -> dict[str, object]:
    """Build the current plan and atomically persist its deterministic report."""
    _validate_release_tag(release_tag)
    layout = WorkspacePaths(root)
    candidates: list[dict[str, object]] = []
    lesson_ids = load_planned_lesson_ids(root)
    for lesson_id in lesson_ids:
        package_dir = layout.lessons_root / lesson_id
        lesson, _metadata = load_lesson_package(package_dir)
        blocks = derive_transcript_blocks(lesson.model_dump(mode="json"))
        identities = plan_lesson_audio_candidates(
            transcript_blocks=blocks,
            settings=settings,
        )
        candidates.extend(_build_candidate_record(lesson_id, identity) for identity in identities)
    unique: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        fingerprint = str(candidate["fingerprint"])
        existing = unique.get(fingerprint)
        if existing is not None and existing["text"] != candidate["text"]:
            raise ValueError(f"audio fingerprint collision for {fingerprint}")
        unique.setdefault(fingerprint, candidate)
    desired_keys = set(unique)
    desired_keys.update(Path(str(candidate["filename"])).stem for candidate in unique.values())
    sources = _discover_audio_sources(root, desired_keys=desired_keys)
    reused, missing_chars = _attach_reuse_evidence(unique, sources)
    missing = len(unique) - reused
    plan: dict[str, object] = {
        "version": K_RELEASE_PLAN_VERSION,
        "source_digest": _calculate_source_digest(root),
        "release_tag": release_tag,
        "github_repository": github_repository,
        "lesson_count": len(lesson_ids),
        "candidate_count": len(candidates),
        "unique_count": len(unique),
        "reused_count": reused,
        "missing_count": missing,
        "missing_characters": missing_chars,
        "estimated_cost_usd": round(missing_chars / 1_000_000 * K_RELEASE_AUDIO_COST_PER_MILLION_CHARS, 2),
        "cache_sources": [str(path) for path in _collect_audio_source_roots(root)],
        "candidates": sorted(unique.values(), key=lambda item: str(item["fingerprint"])),
    }
    plan_path = WorkspacePaths(root).release_scratch_root / K_RELEASE_PLAN_FILENAME
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = plan_path.with_name(f".{plan_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(plan_path)
    plan["plan_path"] = str(plan_path)
    return plan


def _read_existing_plan(root: Path) -> dict[str, object]:
    """Read the prior plan without replacing its source-bound evidence."""
    plan_path = WorkspacePaths(root).release_scratch_root / K_RELEASE_PLAN_FILENAME
    if not plan_path.is_file():
        raise ValueError("no release plan found; run `uv run lesson-data release` first")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read release plan at {plan_path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("version") != K_RELEASE_PLAN_VERSION:
        raise ValueError(f"release plan at {plan_path} has an unsupported version")
    plan["plan_path"] = str(plan_path)
    return plan


def _build_candidate_record(lesson_id: str, identity: AudioCandidateIdentity) -> dict[str, object]:
    """Serialize one identity with enough evidence to review or resume it."""
    return {
        "lesson_id": lesson_id,
        "binding": identity.binding,
        "fingerprint": identity.fingerprint,
        "audio_id": identity.audio_id,
        "filename": identity.filename,
        "voice": identity.voice,
        "text": identity.text,
        "text_chars": len(identity.text),
    }


def _seed_cache(plan: dict[str, object], *, cache_root: Path) -> None:
    """Copy validated historical assets into the canonical fingerprint cache."""
    cache_root.mkdir(parents=True, exist_ok=True)
    raw_candidates = cast(list[object], plan.get("candidates", []))
    for raw in raw_candidates:
        if not isinstance(raw, dict) or not raw.get("reuse_source"):
            continue
        _seed_cache_candidate(raw, cache_root=cache_root)


def _seed_cache_candidate(candidate: dict[object, object], *, cache_root: Path) -> None:
    """Validate and atomically cache one planned historical recording."""
    source = Path(str(candidate["reuse_source"]))
    if not source.is_file():
        raise ValueError(f"planned reusable audio is missing: {source}")
    try:
        validate_audio_asset(source)
    except ValueError as exc:
        raise ValueError(f"planned reusable audio is invalid: {source}") from exc
    destination = cache_root / f"{candidate['fingerprint']}.wav"
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != candidate.get("reuse_sha256"):
        raise ValueError(f"planned reusable audio changed after planning: {source}")
    if destination.is_file() and destination.read_bytes() != source_bytes:
        raise ValueError(f"conflicting reusable audio for fingerprint {candidate['fingerprint']}")
    if destination.exists():
        return
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        validate_audio_asset(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _attach_reuse_evidence(candidates: dict[str, dict[str, object]], sources: dict[str, Path]) -> tuple[int, int]:
    """Bind reusable recording paths and digests, returning reuse and missing-character totals."""
    reused = 0
    missing_chars = 0
    for candidate in candidates.values():
        fingerprint = str(candidate["fingerprint"])
        source = sources.get(fingerprint) or sources.get(Path(str(candidate["filename"])).stem)
        candidate["reuse_source"] = str(source) if source is not None else None
        candidate["reuse_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest() if source is not None else None
        if source is None:
            missing_chars += cast(int, candidate["text_chars"])
        else:
            reused += 1
    return reused, missing_chars


def _discover_audio_sources(root: Path, *, desired_keys: set[str]) -> dict[str, Path]:
    """Index valid cache and historical WAVs by fingerprint or stable UUID stem."""
    index: dict[str, Path] = {}
    for source_root in _collect_audio_source_roots(root):
        for path in sorted(source_root.rglob("*.wav"), key=lambda item: item.as_posix()):
            try:
                validate_audio_asset(path)
            except ValueError:
                continue
            key = path.stem
            if key not in desired_keys:
                continue
            previous = index.get(key)
            if previous is not None and previous.read_bytes() != path.read_bytes():
                raise ValueError(f"conflicting historical audio assets for {key}: {previous} and {path}")
            index.setdefault(key, path)
    return index


def _collect_audio_source_roots(root: Path) -> list[Path]:
    """Return deterministic automatic cache and historical distribution roots."""
    layout = WorkspacePaths(root)
    roots = [layout.audio_cache_root, layout.legacy_audio_cache_root]
    scratch = layout.release_scratch_root.parent
    if scratch.is_dir():
        roots.extend(path for path in sorted(scratch.rglob("audio")) if path.is_dir())
    return list(dict.fromkeys(path for path in roots if path.is_dir()))


def _calculate_source_digest(root: Path) -> str:
    """Hash all source inputs that affect lesson packets and audio identity."""
    layout = WorkspacePaths(root)
    paths = [layout.curriculum_plan, root / "config.yaml", layout.character_registry]
    paths.extend(path for path in sorted(layout.lessons_root.rglob("*")) if path.is_file())
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _build_default_tag() -> str:
    """Return the UTC release tag used when approval omits one."""
    return datetime.now(UTC).strftime("v%Y.%m.%d")


def _find_github_repository(root: Path, configured: str | None) -> str | None:
    """Find the publication repository from arguments, environment, or origin."""
    selected = configured or os.environ.get("GITHUB_REPOSITORY")
    if selected:
        return selected
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=K_RELEASE_GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?", remote)
    return match.group(1) if match else None


def _build_approval_command(plan: dict[str, object]) -> str:
    """Build the exact command that resumes the persisted release plan."""
    command = ["uv", "run", "lesson-data", "release", "--approve", "--tag", str(plan["release_tag"])]
    repository = plan.get("github_repository")
    if isinstance(repository, str) and repository:
        command.extend(("--github-repository", repository))
    return shlex.join(command)


def _validate_release_tag(tag: str) -> None:
    """Require the canonical release-tag shape before using it as a path."""
    if K_RELEASE_TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError("release tag must use the vYYYY.MM.DD format")


def _require_tag_matches_source(root: Path, tag: str, *, repository: str) -> None:
    """Require the selected tag to identify the checked-out source commit."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=K_RELEASE_GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        tagged = subprocess.run(
            ["git", "rev-parse", f"{tag}^{{commit}}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=K_RELEASE_GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        source_changes = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "content/curriculum/plan.yaml",
                "config.yaml",
                "content/authoring/characters.yaml",
                "content/lessons",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=K_RELEASE_GIT_TIMEOUT_SECONDS,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"release tag {tag!r} cannot be resolved to the checked-out commit") from exc
    if tagged != head:
        raise ValueError(f"release tag {tag!r} does not identify the checked-out source commit")
    if source_changes:
        raise ValueError(f"release tag {tag!r} does not include the current source changes")
    try:
        remote_tagged = resolve_release_tag_commit(repository=repository, tag=tag)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"release tag {tag!r} cannot be resolved in GitHub repository {repository!r}") from exc
    if remote_tagged != head:
        raise ValueError(f"release tag {tag!r} in GitHub repository {repository!r} does not identify HEAD")


def _build_plan_summary(plan: dict[str, object]) -> dict[str, object]:
    """Return the bounded plan facts an approval result may echo."""
    keys = (
        "version",
        "source_digest",
        "release_tag",
        "github_repository",
        "lesson_count",
        "candidate_count",
        "unique_count",
        "reused_count",
        "missing_count",
        "missing_characters",
        "estimated_cost_usd",
        "plan_path",
    )
    return {key: plan[key] for key in keys if key in plan}


def _require_audio_complete_packets(root: Path, dist_root: Path) -> None:
    """Require every source-derived audio location and identity in each packet."""
    settings = load_audio_settings(repo_root=root, require_service_account=False)
    for packet_path in sorted((dist_root / "lessons").glob("*.json"), key=lambda item: item.name):
        _require_audio_complete_packet(root, packet_path, settings=settings)


def _require_audio_complete_packet(root: Path, packet_path: Path, *, settings: AudioSettings) -> None:
    """Require source-derived bindings and media identity for one packet."""
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    audio = packet.get("media", {}).get("audio", [])
    if not isinstance(audio, list) or not audio:
        raise ValueError(
            f"release packet {packet_path.stem!r} carries no audio references; "
            "a provider-free text distribution cannot be verified as a release"
        )
    lesson, metadata = load_lesson_package(WorkspacePaths(root).lessons_root / packet_path.stem)
    blocks = derive_transcript_blocks(lesson.model_dump(mode="json"))
    identities = plan_lesson_audio_candidates(transcript_blocks=blocks, settings=settings)
    expected_packet = DistributionService().create_lesson_packet(
        lesson,
        kind=metadata["kind"],
        audio=_build_complete_expected_audio(audio, identities, lesson_id=packet_path.stem),
        audio_bindings={identity.binding: identity.audio_id for identity in identities},
        character_registry=load_optional_character_registry(root, search_parents=False),
        allow_unregistered_characters=True,
    )
    expected_references = _collect_audio_references(expected_packet)
    if _collect_audio_references(packet) != expected_references:
        raise ValueError(f"release packet {packet_path.stem!r} audio bindings do not match canonical source")
    media_ids = {str(entry.get("id")) for entry in audio if isinstance(entry, dict)}
    if media_ids != set(expected_references.values()):
        raise ValueError(f"release packet {packet_path.stem!r} audio inventory does not match canonical source")
    expected_paths = {
        identity.audio_id: f"audio/lessons/{packet_path.stem}/{identity.filename}" for identity in identities
    }
    actual_paths = {str(entry.get("id")): str(entry.get("path")) for entry in audio if isinstance(entry, dict)}
    if actual_paths != expected_paths:
        raise ValueError(f"release packet {packet_path.stem!r} audio media do not match canonical identities")


def _build_complete_expected_audio(
    actual_audio: list[object], identities: list[AudioCandidateIdentity], *, lesson_id: str
) -> list[dict[str, object]]:
    """Supply valid media entries for projecting every expected audio binding."""
    entries = {str(entry.get("id")): entry for entry in actual_audio if isinstance(entry, dict)}
    for identity in identities:
        entries.setdefault(
            identity.audio_id,
            {
                "id": identity.audio_id,
                "path": f"audio/lessons/{lesson_id}/{identity.filename}",
                "url": f"https://invalid.example/audio/lessons/{lesson_id}/{identity.filename}",
                "mime": "audio/wav",
                "status": "synthesized",
            },
        )
    return list(entries.values())


def _collect_audio_references(value: object, path: tuple[object, ...] = ()) -> dict[tuple[object, ...], str]:
    """Collect every public audio binding by its exact packet location."""
    if path[-2:] == ("media", "audio"):
        return {}
    if isinstance(value, list):
        return {
            location: audio_id
            for index, item in enumerate(value)
            for location, audio_id in _collect_audio_references(item, (*path, index)).items()
        }
    if not isinstance(value, dict):
        return {}
    references = {(*path, "audio_id"): value["audio_id"]} if isinstance(value.get("audio_id"), str) else {}
    for key, item in value.items():
        if key != "audio_id":
            references.update(_collect_audio_references(item, (*path, key)))
    return references


def _extract_release_archive(archive_path: Path, extract_root: Path) -> None:
    """Safely extract one audio-free ``dist/`` archive into a fresh root.

    Unsafe member paths, links, unexpected member types, members outside
    ``dist/``, and embedded WAV bytes fail closed before validation runs.
    """
    extract_root.mkdir(parents=True)
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                _extract_release_member(tar=tar, member=member, extract_root=extract_root)
    except tarfile.TarError as exc:
        raise ValueError(f"release archive is not a readable gzip/tar document: {exc}") from exc


def _extract_release_member(*, tar: tarfile.TarFile, member: tarfile.TarInfo, extract_root: Path) -> None:
    """Extract one archive member after proving its path and type are safe."""
    name = member.name
    pure = PurePosixPath(name)
    if "\\" in name or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"release archive member path is unsafe: {name!r}")
    if not (name == K_RELEASE_ARCHIVE_DIST_PREFIX.rstrip("/") or name.startswith(K_RELEASE_ARCHIVE_DIST_PREFIX)):
        raise ValueError(f"release archive member is outside dist/: {name!r}")
    if member.issym() or member.islnk():
        raise ValueError(f"release archive member is a link: {name}")
    destination = extract_root / Path(*pure.parts)
    if member.isdir():
        destination.mkdir(parents=True, exist_ok=True)
        return
    if not member.isfile():
        raise ValueError(f"release archive member is not a regular file: {name}")
    if name.endswith(".wav"):
        raise ValueError(f"release archive must not embed audio bytes: {name}")
    extracted = tar.extractfile(member)
    if extracted is None:
        raise ValueError(f"release archive member has no content: {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(extracted.read())


__all__ = ["run_release", "verify_release"]
