"""Entry point: `plan_workbench_audio`, `recover_workbench_audio_plans`, `synthesize_workbench_audio` and `project_workbench_audio` own draft audio workflows."""

from __future__ import annotations

import hashlib
import json
import math
import re
from _thread import RLock
from collections import Counter
from collections.abc import Callable
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel

from lesson_builder.application.operations.compile_lesson_package import collect_lesson_package
from lesson_builder.application.operations.synthesize_audio import AudioSettings
from lesson_builder.application.operations.synthesize_audio import AudioSynthesisClient
from lesson_builder.application.operations.synthesize_audio import load_audio_settings
from lesson_builder.application.operations.synthesize_audio import plan_lesson_audio_candidates
from lesson_builder.application.operations.synthesize_audio import write_audio_cache_asset
from lesson_builder.application.operations.validate_distribution import inspect_workbench_audio_asset
from lesson_builder.application.operations.validate_distribution import inspect_workbench_audio_bytes
from lesson_builder.application.operations.workbench_records import WorkbenchAudioCheckFinding
from lesson_builder.application.operations.workbench_records import WorkbenchAudioClip
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndex
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndexClip
from lesson_builder.application.operations.workbench_records import WorkbenchAudioPlanRecord
from lesson_builder.application.operations.workbench_records import WorkbenchAudioReleaseVoices
from lesson_builder.application.operations.workbench_records import WorkbenchAudioSpendLedger
from lesson_builder.application.operations.workbench_settings_operations import read_workbench_settings
from lesson_builder.application.settings import K_APPLICATION_AUDIO_COST_PER_MILLION_CHARS_USD
from lesson_builder.application.settings import K_APPLICATION_AUDIO_COST_ROUNDING_INCREMENT_USD
from lesson_builder.clients.tts.google import GoogleTextToSpeechClient
from lesson_builder.domain.lesson.services.audio_bindings import audio_binding_key
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import list_workbench_record_identities
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record

K_WORKBENCH_AUDIO_RECORD_ID = "current"
K_WORKBENCH_AUDIO_SELECTIONS = ("missing", "stale", "missing_and_stale")
K_WORKBENCH_AUDIO_ORIGINS = ("lesson_reading", "lesson_example", "exercise_target")
K_WORKBENCH_AUDIO_SAFE_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")


class WorkbenchAudioPlan(BaseModel):
    """Reviewable cost and clip details returned before synthesis starts."""

    token: str
    lesson_id: str
    scope: Literal["block", "missing_and_stale"]
    selection: Literal["missing", "stale", "missing_and_stale"]
    block_key: str | None
    clips: list[WorkbenchAudioClip]
    new_count: int
    reused_count: int
    characters_to_synthesize: int
    estimated_cost_usd: float
    monthly_spent_usd: float
    monthly_cap_usd: float
    blocked_reason: str | None


def plan_workbench_audio(
    root: Path,
    lesson_id: str,
    scope: Literal["block", "missing_and_stale"],
    *,
    selection: Literal["missing", "stale", "missing_and_stale"] = "missing_and_stale",
    block_key: str | None = None,
    lock: RLock | None = None,
) -> WorkbenchAudioPlan:
    """Capture exact source-bound clips and their cost before author-confirmed synthesis."""
    if scope not in ("block", "missing_and_stale"):
        raise ValueError("scope must be block or missing_and_stale")
    if selection not in K_WORKBENCH_AUDIO_SELECTIONS:
        raise ValueError("selection must be missing, stale or missing_and_stale")
    if scope == "block" and not block_key:
        raise ValueError("block audio plans require a block key")
    candidates, source_digest, settings = _load_audio_candidates(root, lesson_id)
    projection = _project_candidates(root, lesson_id, candidates)
    clips_by_binding = {item["binding"]: item for item in projection["clips"]}
    selected = [
        item
        for item in candidates
        if _is_selected(item, clips_by_binding[item["binding"]], scope, selection, block_key)
    ]
    if not selected:
        raise ValueError("No missing or stale audio clips match this selection")

    unique_new = {item["fingerprint"]: item for item in selected if not _has_reusable_audio(root, item)}
    characters = sum(len(item["text"]) for item in unique_new.values())
    estimate = _rounded_audio_cost(characters)
    ledger = _current_spend_ledger(root)
    spent = _ledger_cost(ledger.synthesized_characters)
    reserved = _ledger_cost(sum(ledger.reservations.values()))
    cap = read_workbench_settings(root).audio.monthly_cap_usd
    blocked_reason = None
    if spent + reserved + estimate > cap + 1e-9:
        blocked_reason = (
            f"This plan would exceed the monthly audio cap of ${cap:.2f}; "
            f"${spent + reserved:.2f} is already spent or reserved."
        )

    token = uuid4().hex
    all_snapshot = _candidate_snapshot(candidates)
    clip_records = [_to_plan_clip(item, force_synthesis=not _has_reusable_audio(root, item)) for item in selected]
    record = WorkbenchAudioPlanRecord(
        token=token,
        lesson_id=lesson_id,
        scope=scope,
        selection=selection,
        block_key=block_key,
        source_digest=source_digest,
        candidate_snapshot_sha256=all_snapshot,
        clips=clip_records,
        characters_to_synthesize=characters,
        estimated_cost_usd=estimate,
        reused_count=len({item["fingerprint"] for item in selected if _has_reusable_audio(root, item)}),
        monthly_spent_usd=spent,
        monthly_cap_usd=cap,
        blocked_reason=blocked_reason,
    )
    context = lock if lock is not None else nullcontext()
    with context:
        write_workbench_record(root, "audio_plans", token, record)
    return WorkbenchAudioPlan(
        token=token,
        lesson_id=lesson_id,
        scope=scope,
        selection=selection,
        block_key=block_key,
        clips=clip_records,
        new_count=len(unique_new),
        reused_count=record.reused_count,
        characters_to_synthesize=characters,
        estimated_cost_usd=estimate,
        monthly_spent_usd=spent,
        monthly_cap_usd=cap,
        blocked_reason=blocked_reason,
    )


def recover_workbench_audio_plans(root: Path, lock: RLock) -> tuple[str, ...]:
    """Fail interrupted synthesis plans and reconcile their persisted budget reservations."""
    recovered: list[str] = []
    with lock:
        spend_months = list_workbench_record_identities(root, "audio_spend")
        ledgers = {month: _spend_ledger(root, month) for month in spend_months}
        for token in list_workbench_record_identities(root, "audio_plans"):
            record = read_workbench_record(root, "audio_plans", token, WorkbenchAudioPlanRecord)
            reserved_months = [month for month, ledger in ledgers.items() if token in ledger.reservations]
            if record.status == "pending":
                _release_audio_reservation_entries(root, token, ledgers, reserved_months)
            elif record.status == "running":
                _mark_interrupted_audio_plan(root, token, record, ledgers, reserved_months)
                recovered.append(token)
                _reconcile_interrupted_audio_reservation(root, token, record, ledgers, reserved_months)
            elif record.status == "failed" and record.recovery_month is not None:
                _reconcile_interrupted_audio_reservation(root, token, record, ledgers, reserved_months)
            elif record.status in ("failed", "completed"):
                _release_audio_reservation_entries(root, token, ledgers, reserved_months)
    return tuple(recovered)


def synthesize_workbench_audio(
    root: Path,
    plan_token: str,
    *,
    synthesis_client: AudioSynthesisClient | None = None,
    lock: RLock | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Run one pending confirmed plan and persist validated bytes, checks and spend."""
    record, candidates, settings = _claim_audio_plan(root, plan_token, lock)
    owned_client: GoogleTextToSpeechClient | None = None
    client = synthesis_client
    if client is None:
        if not settings.service_account_path.is_file():
            _finish_failed_plan(root, plan_token, lock, "Google TTS credentials are not configured")
            raise ValueError("Google TTS credentials are not configured")
        owned_client = GoogleTextToSpeechClient(
            service_account_path=settings.service_account_path,
            language=settings.language,
        )
        client = owned_client
    try:
        result = _execute_audio_plan(root, record, candidates, client, lock, on_progress)
    except Exception as exc:
        succeeded_chars = int(record.result.get("spent_characters", 0))
        _finish_failed_plan(root, plan_token, lock, str(exc), succeeded_chars=succeeded_chars)
        raise
    finally:
        if owned_client is not None:
            owned_client.close()
    if on_progress is not None:
        on_progress("Audio checks complete")
    return result


def project_workbench_audio(root: Path, lesson_id: str, *, lesson: dict[str, Any] | None = None) -> dict[str, object]:
    """Project live clip state, author checks and last-release voice changes for one lesson."""
    try:
        if lesson is None:
            candidates, _, _ = _load_audio_candidates(root, lesson_id)
        else:
            settings = load_audio_settings(repo_root=root, require_service_account=False)
            candidates = _audio_candidates(lesson_id, lesson, settings)
        projection = _project_candidates(root, lesson_id, candidates)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return {
            "clips": [],
            "voices": [],
            "missing_count": 0,
            "stale_count": 0,
            "ready_count": 0,
            "blockers": [f"Audio unavailable: {exc}"],
        }
    return {
        "clips": projection["clips"],
        "voices": projection["voices"],
        "missing_count": sum(item["status"] == "missing" for item in projection["clips"]),
        "stale_count": sum(item["status"] == "stale" for item in projection["clips"]),
        "ready_count": sum(item["status"] == "ready" for item in projection["clips"]),
        "blockers": projection["blockers"],
    }


def read_workbench_audio_asset(root: Path, lesson_id: str, fingerprint: str) -> Path:
    """Resolve a current lesson audio clip inside the shared cache for HTTP playback."""
    if not K_WORKBENCH_AUDIO_SAFE_FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("Invalid audio fingerprint")
    candidates, _, _ = _load_audio_candidates(root, lesson_id)
    if fingerprint not in {item["fingerprint"] for item in candidates}:
        raise ValueError("Audio clip is not part of the current lesson")
    path = _cache_path(root, fingerprint)
    candidate = next(item for item in candidates if item["fingerprint"] == fingerprint)
    inspection = inspect_workbench_audio_asset(path, text=candidate["text"])
    if inspection.findings:
        raise ValueError("Audio clip is unavailable")
    return path


def record_release_audio_voices(root: Path, tag: str, lessons: Mapping[str, Mapping[str, str]]) -> None:
    """Replace the ignored voice baseline only after a standard release publishes successfully."""
    write_workbench_record(
        root,
        "audio_release",
        K_WORKBENCH_AUDIO_RECORD_ID,
        WorkbenchAudioReleaseVoices(
            tag=tag, lessons={lesson_id: dict(voices) for lesson_id, voices in lessons.items()}
        ),
    )


def _claim_audio_plan(
    root: Path, plan_token: str, lock: RLock | None
) -> tuple[WorkbenchAudioPlanRecord, list[dict[str, Any]], AudioSettings]:
    """Revalidate a stored confirmation, enforce the current cap and reserve its characters."""
    if not re.fullmatch(r"[a-f0-9]{32}", plan_token):
        raise ValueError("Invalid audio confirmation token")
    context = lock if lock is not None else nullcontext()
    with context:
        if plan_token not in list_workbench_record_identities(root, "audio_plans"):
            raise ValueError("Audio confirmation has expired; review a new plan")
        record = read_workbench_record(root, "audio_plans", plan_token, WorkbenchAudioPlanRecord)
        if record.status != "pending":
            raise ValueError("Audio confirmation has already been used")
        if record.blocked_reason:
            raise ValueError(record.blocked_reason)
        candidates, source_digest, settings = _load_audio_candidates(root, record.lesson_id)
        if source_digest != record.source_digest or _candidate_snapshot(candidates) != record.candidate_snapshot_sha256:
            raise ValueError("Lesson audio changed after the estimate; review a new cost confirmation")
        by_binding = {candidate["binding"]: candidate for candidate in candidates}
        for clip in record.clips:
            has_reusable_audio = _has_reusable_audio(root, by_binding[clip.binding])
            if has_reusable_audio == clip.force_synthesis:
                raise ValueError("Audio cache changed after the estimate; review a new cost confirmation")
        _reserve_audio_budget(root, record, plan_token)
        record.status = "running"
        write_workbench_record(root, "audio_plans", plan_token, record)
    return record, candidates, settings


def _reserve_audio_budget(root: Path, record: WorkbenchAudioPlanRecord, plan_token: str) -> None:
    """Reserve the confirmed estimate against this month's current cap."""
    month = _month_key()
    cap = read_workbench_settings(root).audio.monthly_cap_usd
    ledger = _spend_ledger(root, month)
    committed = ledger.synthesized_characters + sum(ledger.reservations.values())
    if _ledger_cost(committed) + record.estimated_cost_usd > cap + 1e-9:
        raise ValueError(f"The monthly audio cap of ${cap:.2f} would be exceeded")
    ledger.reservations[plan_token] = record.characters_to_synthesize
    record.reservation_month = month
    _write_spend_ledger(root, month, ledger)


def _ensure_current_month_audio_reservation(root: Path, record: WorkbenchAudioPlanRecord, lock: RLock | None) -> None:
    """Move remaining confirmed spend into the month of the next provider request."""
    context = lock if lock is not None else nullcontext()
    with context:
        persisted = read_workbench_record(root, "audio_plans", record.token, WorkbenchAudioPlanRecord)
        old_month = persisted.reservation_month
        if old_month is None:
            raise ValueError("Audio budget reservation has no recorded month")
        month = _month_key()
        if old_month == month:
            return
        old_ledger = _spend_ledger(root, old_month)
        month_ledger = _spend_ledger(root, month)
        spent_characters = int(persisted.result.get("spent_characters", 0))
        remaining = max(
            old_ledger.reservations.get(record.token, 0),
            month_ledger.reservations.get(record.token, 0),
            persisted.characters_to_synthesize - spent_characters,
        )
        cap = read_workbench_settings(root).audio.monthly_cap_usd
        other_commitments = month_ledger.synthesized_characters + sum(
            characters for token, characters in month_ledger.reservations.items() if token != record.token
        )
        if _ledger_cost(other_commitments) + _rounded_audio_cost(remaining) > cap + 1e-9:
            raise ValueError(f"The monthly audio cap of ${cap:.2f} would be exceeded")
        month_ledger.reservations[record.token] = remaining
        _write_spend_ledger(root, month, month_ledger)
        old_ledger.reservations.pop(record.token, None)
        _write_spend_ledger(root, old_month, old_ledger)
        persisted.reservation_month = month
        write_workbench_record(root, "audio_plans", record.token, persisted)
        record.reservation_month = month


def _execute_audio_plan(
    root: Path,
    record: WorkbenchAudioPlanRecord,
    candidates: list[dict[str, Any]],
    client: AudioSynthesisClient,
    lock: RLock | None,
    on_progress: Callable[[str], None] | None,
) -> dict[str, object]:
    """Synthesize unique cache identities, check each selected binding and finalize records."""
    by_binding = {item["binding"]: item for item in candidates}
    selected = [by_binding[clip.binding] for clip in record.clips]
    unique = {item["fingerprint"]: item for item in selected if not _has_reusable_audio(root, item)}
    index = _read_audio_index(root, record.lesson_id)
    errors: list[str] = []
    succeeded_chars = 0
    try:
        for position, candidate in enumerate(unique.values(), start=1):
            if on_progress is not None:
                on_progress(f"Generating audio {position} of {len(unique)}")
            findings = _synthesize_audio_candidate(
                root, record, candidate, selected, index, client, succeeded_chars, lock
            )
            succeeded_chars += len(candidate["text"])
            errors.extend(finding.message for finding in findings)
        _index_reused_audio(root, record, selected, unique, index)
    except Exception:
        _persist_audio_index_updates(root, record.lesson_id, index, selected, lock)
        raise
    _finish_audio_plan(root, record, index, selected, lock, errors)
    return {"completed_count": len(selected), "errors": errors}


def _synthesize_audio_candidate(
    root: Path,
    record: WorkbenchAudioPlanRecord,
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
    index: WorkbenchAudioIndex,
    client: AudioSynthesisClient,
    succeeded_chars: int,
    lock: RLock | None,
) -> tuple[Any, ...]:
    """Synthesize and inspect one unique request, binding results to each source occurrence."""
    _ensure_current_month_audio_reservation(root, record, lock)
    audio_bytes = client.synthesize(text=candidate["text"], voice=candidate["voice"])
    succeeded_chars += len(candidate["text"])
    record.result["spent_characters"] = succeeded_chars
    _persist_successful_provider_call(root, record.token, succeeded_chars, lock)
    inspection = inspect_workbench_audio_bytes(audio_bytes, text=candidate["text"])
    if not any(finding.code == "invalid_wav" for finding in inspection.findings):
        write_audio_cache_asset(
            cache_root=WorkspacePaths(root).audio_cache_root,
            fingerprint=candidate["fingerprint"],
            audio_bytes=audio_bytes,
        )
    for binding_candidate in selected:
        if binding_candidate["fingerprint"] == candidate["fingerprint"]:
            _record_index_clip(
                index, binding_candidate, record.source_digest, inspection.duration_ms, inspection.findings
            )
    return inspection.findings


def _index_reused_audio(
    root: Path,
    record: WorkbenchAudioPlanRecord,
    selected: list[dict[str, Any]],
    unique: Mapping[str, dict[str, Any]],
    index: WorkbenchAudioIndex,
) -> None:
    """Refresh checks for exact cache hits without charging synthesis spend."""
    for candidate in selected:
        if candidate["fingerprint"] not in unique:
            inspection = inspect_workbench_audio_asset(
                _cache_path(root, candidate["fingerprint"]), text=candidate["text"]
            )
            _record_index_clip(index, candidate, record.source_digest, inspection.duration_ms, inspection.findings)


def _finish_audio_plan(
    root: Path,
    record: WorkbenchAudioPlanRecord,
    index: WorkbenchAudioIndex,
    selected: list[dict[str, Any]],
    lock: RLock | None,
    errors: list[str],
) -> None:
    """Persist all per-binding checks and release the remaining monthly reservation."""
    _persist_audio_index_updates(root, record.lesson_id, index, selected, lock)
    context = lock if lock is not None else nullcontext()
    with context:
        reservation_month = record.reservation_month
        if reservation_month is None:
            raise ValueError("Audio budget reservation has no recorded month")
        ledger = _spend_ledger(root, reservation_month)
        ledger.reservations.pop(record.token, None)
        _write_spend_ledger(root, reservation_month, ledger)
        record.status = "completed"
        record.result = {"completed_count": len(selected), "errors": errors}
        write_workbench_record(root, "audio_plans", record.token, record)


def _persist_audio_index_updates(
    root: Path,
    lesson_id: str,
    updated_index: WorkbenchAudioIndex,
    selected: list[dict[str, Any]],
    lock: RLock | None,
) -> None:
    """Merge completed binding checks into the latest index while serialized with other jobs."""
    context = lock if lock is not None else nullcontext()
    with context:
        current = _read_audio_index(root, lesson_id)
        for candidate in selected:
            binding = candidate["binding"]
            updated = updated_index.clips.get(binding)
            if updated is not None:
                current.clips[binding] = updated
        write_workbench_record(root, "audio_index", lesson_id, current)


def _load_audio_candidates(root: Path, lesson_id: str) -> tuple[list[dict[str, Any]], str, AudioSettings]:
    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package

    package = resolve_lesson_package(root, lesson_id)
    compiled = collect_lesson_package(package.path)
    if not compiled.is_valid or compiled.lesson is None:
        raise ValueError("Audio requires a valid lesson source")
    settings = load_audio_settings(repo_root=root, require_service_account=False)
    lesson = compiled.lesson.model_dump(mode="json")
    return _audio_candidates(lesson_id, lesson, settings), compiled.source_digest, settings


def _audio_candidates(lesson_id: str, lesson: dict[str, Any], settings: AudioSettings) -> list[dict[str, Any]]:
    """Derive audio candidates from one already compiled lesson value."""
    transcript = derive_transcript_blocks(lesson)
    identities = plan_lesson_audio_candidates(transcript_blocks=transcript, settings=settings)
    candidates: list[dict[str, Any]] = []
    for block, identity in zip(
        (item for item in transcript if item.get("origin") in K_WORKBENCH_AUDIO_ORIGINS), identities, strict=True
    ):
        source = block.get("source")
        if not isinstance(source, Mapping):
            continue
        binding = audio_binding_key(source)
        speaker_id = source.get("speaker_id") if isinstance(source.get("speaker_id"), str) else None
        candidates.append(
            {
                "binding": binding,
                "row_key": _row_key(source),
                "fingerprint": identity.fingerprint,
                "audio_id": identity.audio_id,
                "filename": identity.filename,
                "text": identity.text,
                "translation": _translation_for_source(lesson, source),
                "voice": identity.voice,
                "speaker_id": speaker_id,
                "speaker_name": source.get("speaker_name") if isinstance(source.get("speaker_name"), str) else None,
                "origin": block["origin"],
                "source": dict(source),
                "characters": len(identity.text),
            }
        )
    return candidates


def _project_candidates(root: Path, lesson_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    index = _read_audio_index(root, lesson_id)
    released = read_workbench_record(root, "audio_release", K_WORKBENCH_AUDIO_RECORD_ID, WorkbenchAudioReleaseVoices)
    baseline = released.lessons.get(lesson_id, {})
    clips = [_project_candidate(root, lesson_id, candidate, index) for candidate in candidates]
    voices = _project_voice_assignments(candidates, baseline)
    return {"clips": clips, "voices": voices, "blockers": _audio_blockers(clips)}


def _project_candidate(
    root: Path, lesson_id: str, candidate: dict[str, Any], index: WorkbenchAudioIndex
) -> dict[str, Any]:
    """Project one transcript binding with its live cache and stored checks."""
    previous = index.clips.get(candidate["binding"])
    cache_path = _cache_path(root, candidate["fingerprint"])
    exists = cache_path.is_file() and not cache_path.is_symlink()
    duration_ms, findings = _inspect_cached_candidate(cache_path, candidate["text"], exists)
    duration_ms, findings = _merge_indexed_findings(previous, candidate, duration_ms, findings)
    stale = bool(previous and previous.fingerprint != candidate["fingerprint"])
    return {
        **candidate,
        "status": _audio_clip_status(exists, findings, stale),
        "duration_ms": duration_ms,
        "findings": findings,
        "play_url": f"/api/lessons/{lesson_id}/audio/{candidate['fingerprint']}",
    }


def _inspect_cached_candidate(cache_path: Path, text: str, exists: bool) -> tuple[int | None, list[dict[str, str]]]:
    """Return the live duration and audio findings for a present shared-cache asset."""
    if not exists:
        return None, []
    inspection = inspect_workbench_audio_asset(cache_path, text=text)
    return inspection.duration_ms, [
        {"code": finding.code, "message": finding.message} for finding in inspection.findings
    ]


def _merge_indexed_findings(
    previous: WorkbenchAudioIndexClip | None,
    candidate: dict[str, Any],
    duration_ms: int | None,
    findings: list[dict[str, str]],
) -> tuple[int | None, list[dict[str, str]]]:
    """Keep stored findings for an unchanged fingerprint when no live finding supersedes them."""
    if previous is None or previous.fingerprint != candidate["fingerprint"]:
        return duration_ms, findings
    finding_codes = {finding["code"] for finding in findings}
    findings.extend(
        {"code": finding.code, "message": finding.message}
        for finding in previous.findings
        if finding.code not in finding_codes
    )
    return duration_ms if duration_ms is not None else previous.duration_ms, findings


def _audio_clip_status(exists: bool, findings: list[dict[str, str]], stale: bool) -> str:
    """Return the author-visible state for one cache binding."""
    if exists and not findings:
        return "ready"
    if stale or findings:
        return "stale"
    return "missing"


def _project_voice_assignments(candidates: list[dict[str, Any]], baseline: dict[str, str]) -> list[dict[str, object]]:
    """Project distinct speaker voices and differences from the last release."""
    voices: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        if not candidate["speaker_id"]:
            continue
        speaker = str(candidate["speaker_id"])
        released_voice = baseline.get(speaker)
        voices[speaker] = {
            "speaker_id": speaker,
            "speaker_name": candidate["speaker_name"] or speaker,
            "voice": candidate["voice"],
            "changed_since_release": released_voice is not None and released_voice != candidate["voice"],
            "released_voice": released_voice,
        }
    return list(voices.values())


def _audio_blockers(clips: list[dict[str, Any]]) -> list[str]:
    """Summarize missing, stale, and checked clips for the promotion gate."""
    missing_count = sum(item["status"] == "missing" for item in clips)
    stale_count = sum(item["status"] == "stale" for item in clips)
    finding_counts = Counter(finding["code"] for clip in clips for finding in clip["findings"])
    blockers: list[str] = []
    if missing_count:
        blockers.append(f"{missing_count} missing audio clips")
    if stale_count:
        blockers.append(f"{stale_count} stale audio clips")
    if finding_counts:
        details = ", ".join(f"{count} {code.replace('_', ' ')}" for code, count in sorted(finding_counts.items()))
        blockers.append(f"{sum(finding_counts.values())} open audio findings: {details}")
    return blockers


def _is_selected(
    candidate: dict[str, Any], status: dict[str, Any], scope: str, selection: str, block_key: str | None
) -> bool:
    if scope == "block" and candidate["row_key"] != block_key:
        return False
    if status["status"] == "ready":
        return False
    return selection == "missing_and_stale" or status["status"] == selection


def _to_plan_clip(candidate: dict[str, Any], *, force_synthesis: bool) -> WorkbenchAudioClip:
    return WorkbenchAudioClip(**candidate, force_synthesis=force_synthesis)


def _candidate_snapshot(candidates: list[dict[str, Any]]) -> str:
    payload = [
        {key: value for key, value in candidate.items() if key not in ("translation",)} for candidate in candidates
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _has_reusable_audio(root: Path, candidate: dict[str, Any]) -> bool:
    path = _cache_path(root, candidate["fingerprint"])
    if not path.is_file() or path.is_symlink():
        return False
    inspection = inspect_workbench_audio_asset(path, text=candidate["text"])
    return inspection.duration_ms is not None and not inspection.findings


def _cache_path(root: Path, fingerprint: str) -> Path:
    if not K_WORKBENCH_AUDIO_SAFE_FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("Invalid audio fingerprint")
    workspace_root = Path(root).resolve()
    cache_root = WorkspacePaths(workspace_root).audio_cache_root
    path = cache_root / f"{fingerprint}.wav"
    symlinked_parent = any(
        parent.is_symlink()
        for parent in cache_root.parents
        if parent == workspace_root or parent.is_relative_to(workspace_root)
    )
    if (
        cache_root.is_symlink()
        or symlinked_parent
        or path.is_symlink()
        or not path.resolve().is_relative_to(cache_root.resolve())
    ):
        raise ValueError("Audio cache path escapes the shared cache")
    return path


def _read_audio_index(root: Path, lesson_id: str) -> WorkbenchAudioIndex:
    return read_workbench_record(root, "audio_index", lesson_id, WorkbenchAudioIndex)


def _record_index_clip(
    index: WorkbenchAudioIndex,
    candidate: dict[str, Any],
    source_digest: str,
    duration_ms: int | None,
    findings: tuple[Any, ...],
) -> None:
    index.clips[candidate["binding"]] = WorkbenchAudioIndexClip(
        fingerprint=candidate["fingerprint"],
        source_digest=source_digest,
        voice=candidate["voice"],
        audio_id=candidate["audio_id"],
        duration_ms=duration_ms,
        findings=[WorkbenchAudioCheckFinding(code=finding.code, message=finding.message) for finding in findings],
    )


def _current_spend_ledger(root: Path) -> WorkbenchAudioSpendLedger:
    return _spend_ledger(root, _month_key())


def _spend_ledger(root: Path, month: str) -> WorkbenchAudioSpendLedger:
    return read_workbench_record(root, "audio_spend", month, WorkbenchAudioSpendLedger)


def _write_spend_ledger(root: Path, month: str, ledger: WorkbenchAudioSpendLedger) -> None:
    write_workbench_record(root, "audio_spend", month, ledger)


def _mark_interrupted_audio_plan(
    root: Path,
    token: str,
    record: WorkbenchAudioPlanRecord,
    ledgers: dict[str, WorkbenchAudioSpendLedger],
    reserved_months: list[str],
) -> None:
    """Persist the conservative interrupted outcome before releasing its reservation."""
    owner_month = record.reservation_month
    if owner_month not in reserved_months:
        owner_month = max(
            reserved_months,
            key=lambda month: (ledgers[month].reservations[token], month),
            default=record.reservation_month,
        )
    reserved = ledgers[owner_month].reservations.get(token, 0) if owner_month in ledgers else 0
    record.status = "failed"
    record.recovery_month = owner_month
    record.recovery_reserved_characters = reserved
    record.result = {"error": "interrupted", "spent_characters": record.characters_to_synthesize}
    write_workbench_record(root, "audio_plans", token, record)


def _release_audio_reservation_entries(
    root: Path, token: str, ledgers: dict[str, WorkbenchAudioSpendLedger], months: list[str]
) -> None:
    """Remove reservations that were written before a pending plan became running."""
    for month in months:
        ledgers[month].reservations.pop(token, None)
        _write_spend_ledger(root, month, ledgers[month])


def _reconcile_interrupted_audio_reservation(
    root: Path,
    token: str,
    record: WorkbenchAudioPlanRecord,
    ledgers: dict[str, WorkbenchAudioSpendLedger],
    reserved_months: list[str],
) -> None:
    """Charge the captured uncertain remainder once, then remove duplicate reservation entries."""
    owner_month = record.recovery_month
    if owner_month is None:
        return
    owner_ledger = ledgers.setdefault(owner_month, _spend_ledger(root, owner_month))
    if token in owner_ledger.reservations:
        owner_ledger.synthesized_characters += record.recovery_reserved_characters
        owner_ledger.reservations.pop(token, None)
        _write_spend_ledger(root, owner_month, owner_ledger)
    for month in reserved_months:
        if month == owner_month:
            continue
        ledger = ledgers[month]
        if token in ledger.reservations:
            ledger.reservations.pop(token, None)
            _write_spend_ledger(root, month, ledger)


def _month_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _ledger_cost(characters: int) -> float:
    return characters / 1_000_000 * K_APPLICATION_AUDIO_COST_PER_MILLION_CHARS_USD


def _rounded_audio_cost(characters: int) -> float:
    raw = _ledger_cost(characters)
    increment = K_APPLICATION_AUDIO_COST_ROUNDING_INCREMENT_USD
    return round(math.ceil(raw / increment) * increment, 2) if raw else 0.0


def _persist_successful_provider_call(root: Path, token: str, spent_characters: int, lock: RLock | None) -> None:
    context = lock if lock is not None else nullcontext()
    with context:
        plan = read_workbench_record(root, "audio_plans", token, WorkbenchAudioPlanRecord)
        month = plan.reservation_month
        if month is None:
            raise ValueError("Audio budget reservation has no recorded month")
        ledger = _spend_ledger(root, month)
        previous = ledger.reservations.get(token, 0)
        already_spent = int(plan.result.get("spent_characters", 0))
        delta = max(0, spent_characters - already_spent)
        ledger.synthesized_characters += delta
        ledger.reservations[token] = max(0, previous - delta)
        plan.result["spent_characters"] = spent_characters
        _write_spend_ledger(root, month, ledger)
        write_workbench_record(root, "audio_plans", token, plan)


def _finish_failed_plan(root: Path, token: str, lock: RLock | None, error: str, *, succeeded_chars: int = 0) -> None:
    context = lock if lock is not None else nullcontext()
    with context:
        plan = read_workbench_record(root, "audio_plans", token, WorkbenchAudioPlanRecord)
        month = plan.reservation_month
        if month is None:
            raise ValueError("Audio budget reservation has no recorded month")
        ledger = _spend_ledger(root, month)
        accounted = int(plan.result.get("spent_characters", 0))
        total_spent = max(succeeded_chars, accounted)
        reserved = ledger.reservations.get(token, 0)
        already_committed = max(accounted, plan.characters_to_synthesize - reserved)
        delta = max(0, total_spent - already_committed)
        ledger.synthesized_characters += delta
        ledger.reservations.pop(token, None)
        _write_spend_ledger(root, month, ledger)
        plan.status = "failed"
        plan.result = {"error": error, "spent_characters": total_spent}
        write_workbench_record(root, "audio_plans", token, plan)


def _row_key(source: Mapping[str, Any]) -> str:
    exercise_id = source.get("exercise_id")
    if isinstance(exercise_id, str):
        return exercise_id
    section_id = source.get("section_id")
    path = source.get("block_path")
    block_index = path[0] if isinstance(path, list) and path else source.get("block_index")
    return f"{section_id}#{block_index}" if isinstance(section_id, str) else ""


def _translation_for_source(lesson: dict[str, Any], source: Mapping[str, Any]) -> str | None:
    elements = lesson.get("elements")
    if not isinstance(elements, list):
        return None
    section = next(
        (
            item
            for item in elements
            if isinstance(item, dict)
            and item.get("element_kind") == "section"
            and item.get("id") == source.get("section_id")
        ),
        None,
    )
    if section is None or not isinstance(section.get("blocks"), list):
        return None
    block_path = source.get("block_path")
    indexes = block_path if isinstance(block_path, list) else [source.get("block_index")]
    current: Any = section["blocks"]
    try:
        for index in indexes:
            current = current[index]
        item_index = source.get("item_index")
        if item_index is not None:
            current = current.get("items", [])[item_index]
        value = current.get("translation") or current.get("en")
        if isinstance(value, str):
            return value
        return _flatten_spans(value) if isinstance(value, list) else None
    except (IndexError, KeyError, TypeError):
        return None


def _flatten_spans(value: list[object]) -> str | None:
    words = [item.get("value", "") for item in value if isinstance(item, dict) and item.get("kind") == "text"]
    text = "".join(str(word) for word in words).strip()
    return text or None


__all__ = [
    "WorkbenchAudioPlan",
    "plan_workbench_audio",
    "project_workbench_audio",
    "read_workbench_audio_asset",
    "record_release_audio_voices",
    "synthesize_workbench_audio",
]
