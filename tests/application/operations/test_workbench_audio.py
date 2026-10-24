"""Entry points: `plan_workbench_audio`, `synthesize_workbench_audio` and `project_workbench_audio` behavior tests."""

from __future__ import annotations

import shutil
import wave
from _thread import RLock
from array import array
from io import BytesIO
from pathlib import Path
from threading import Barrier
from threading import Thread

import pytest

from lesson_builder.application.operations import workbench_audio
from lesson_builder.application.operations.compile_lesson_package import collect_lesson_package
from lesson_builder.application.operations.synthesize_audio import write_audio_cache_asset
from lesson_builder.application.operations.validate_distribution import inspect_workbench_audio_bytes
from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_audio import project_workbench_audio
from lesson_builder.application.operations.workbench_audio import read_workbench_audio_asset
from lesson_builder.application.operations.workbench_audio import record_release_audio_voices
from lesson_builder.application.operations.workbench_audio import synthesize_workbench_audio
from lesson_builder.application.operations.workbench_records import WorkbenchAudioCheckFinding
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndex
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndexClip
from lesson_builder.application.operations.workbench_records import WorkbenchAudioPlanRecord
from lesson_builder.application.operations.workbench_records import WorkbenchAudioSpendLedger
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchAudioSettings
from lesson_builder.application.operations.workbench_settings_operations import default_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import read_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import write_workbench_settings
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record
from tests.application.fakes import FakeTtsClient
from tests.application.fakes import fake_audio_for_text
from tests.settings import K_TEST_HANG_GUARD_SECONDS


def test_plan_workbench_audio_given_missing_block_expect_source_bound_cost_estimate(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)

    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")

    assert plan.new_count == 1
    assert plan.characters_to_synthesize == len(plan.clips[0].text)
    assert plan.estimated_cost_usd == 0.01
    assert plan.clips[0].translation == "Hi! My name is Mina. What’s your name?"
    assert plan.clips[0].binding == "section:a-first-meeting:block:1"
    assert len(plan.token) == 32


def test_project_workbench_audio_given_compiled_lesson_expect_same_projection_without_recompiling_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _audio_workspace(tmp_path)
    package = root / "content" / "lessons" / "greet_and_introduce_yourself"
    compiled = collect_lesson_package(package)
    assert compiled.lesson is not None
    lesson = compiled.lesson.model_dump(mode="json")
    expected = project_workbench_audio(root, "greet_and_introduce_yourself")

    def fail_if_compiled(*args: object, **kwargs: object) -> object:
        raise AssertionError("audio projection recompiled an already compiled lesson")

    monkeypatch.setattr(workbench_audio, "collect_lesson_package", fail_if_compiled)

    actual = project_workbench_audio(root, "greet_and_introduce_yourself", lesson=lesson)

    assert actual == expected


def test_synthesize_workbench_audio_given_confirmed_token_expect_shared_cache_and_ready_audio(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    client = FakeTtsClient(_workbench_wav())

    result = synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert result == {"completed_count": 1, "errors": []}
    assert len(client.calls) == 1
    audio = project_workbench_audio(root, "greet_and_introduce_yourself")
    clip = next(item for item in audio["clips"] if item["binding"] == plan.clips[0].binding)
    assert clip["status"] == "ready"
    assert read_workbench_audio_asset(root, "greet_and_introduce_yourself", plan.clips[0].fingerprint).is_file()


def test_synthesize_workbench_audio_given_ledger_write_fails_after_provider_success_expect_characters_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    client = FakeTtsClient(_workbench_wav())
    original_write = workbench_audio.write_workbench_record
    ledger_write_failed = False

    def fail_first_post_provider_ledger_write(workspace: Path, collection: str, identity: str, record: object) -> None:
        nonlocal ledger_write_failed
        if collection == "audio_spend" and client.calls and not ledger_write_failed:
            ledger_write_failed = True
            raise OSError("spend ledger write failed after provider success")
        original_write(workspace, collection, identity, record)

    monkeypatch.setattr(workbench_audio, "write_workbench_record", fail_first_post_provider_ledger_write)

    with pytest.raises(OSError, match="spend ledger write failed after provider success"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    failed_plan = read_workbench_record(root, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
    assert failed_plan.reservation_month is not None
    ledger = read_workbench_record(root, "audio_spend", failed_plan.reservation_month, WorkbenchAudioSpendLedger)
    assert ledger_write_failed
    assert ledger.synthesized_characters == len(plan.clips[0].text)
    assert plan.token not in ledger.reservations
    assert failed_plan.result["spent_characters"] == len(plan.clips[0].text)

    settings = read_workbench_settings(root).model_copy(update={"audio": WorkbenchAudioSettings(monthly_cap_usd=0.01)})
    write_workbench_settings(root, settings)
    retry_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    assert retry_plan.blocked_reason is not None


def test_synthesize_workbench_audio_given_changed_source_expect_reconfirmation_without_provider_call(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    lesson_path = root / "content" / "lessons" / "greet_and_introduce_yourself" / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace("Hei! Jeg heter Mina.", "Hei! Jeg heter Mina, ja."),
        encoding="utf-8",
    )
    client = FakeTtsClient(_workbench_wav())

    with pytest.raises(ValueError, match="review a new cost confirmation"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert client.calls == []


def test_synthesize_workbench_audio_given_cache_changed_after_plan_expect_reconfirmation_without_provider_call(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    write_audio_cache_asset(
        cache_root=WorkspacePaths(root).audio_cache_root,
        fingerprint=plan.clips[0].fingerprint,
        audio_bytes=_workbench_wav(),
    )
    client = FakeTtsClient(_workbench_wav())

    with pytest.raises(ValueError, match="Audio cache changed after the estimate"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert client.calls == []


def test_synthesize_workbench_audio_given_monthly_cap_reached_after_plan_expect_block_without_provider_call(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    settings = read_workbench_settings(root).model_copy(update={"audio": WorkbenchAudioSettings(monthly_cap_usd=0)})
    write_workbench_settings(root, settings)
    client = FakeTtsClient(_workbench_wav())

    with pytest.raises(ValueError, match="monthly audio cap.*exceeded"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert client.calls == []


def test_synthesize_workbench_audio_given_month_rollover_expect_remaining_reservation_rechecked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=100.0)
    current_month = ["2026-10"]
    monkeypatch.setattr("lesson_builder.application.operations.workbench_audio._month_key", lambda: current_month[0])
    primary_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="missing")
    competing_plan = plan_workbench_audio(
        root,
        "greet_and_introduce_yourself",
        "block",
        block_key=primary_plan.clips[0].row_key,
        selection="missing",
    )
    assert len(primary_plan.clips) > 1
    assert primary_plan.blocked_reason is None
    assert competing_plan.blocked_reason is None
    settings = read_workbench_settings(root).model_copy(
        update={"audio": WorkbenchAudioSettings(monthly_cap_usd=primary_plan.estimated_cost_usd)}
    )
    write_workbench_settings(root, settings)
    competing_client = FakeTtsClient.from_fn(fake_audio_for_text)
    competing_results: list[dict[str, object]] = []

    def synthesize_after_rollover(text: str, voice: str) -> bytes:
        if not competing_results:
            current_month[0] = "2026-11"
            competing_results.append(
                synthesize_workbench_audio(root, competing_plan.token, synthesis_client=competing_client)
            )
        return fake_audio_for_text(text, voice)

    primary_client = FakeTtsClient.from_fn(synthesize_after_rollover)

    with pytest.raises(ValueError, match="monthly audio cap.*exceeded"):
        synthesize_workbench_audio(root, primary_plan.token, synthesis_client=primary_client)

    assert len(primary_client.calls) == 1
    assert len(competing_client.calls) == 1
    assert competing_results == [{"completed_count": 1, "errors": []}]
    october = read_workbench_record(root, "audio_spend", "2026-10", WorkbenchAudioSpendLedger)
    november = read_workbench_record(root, "audio_spend", "2026-11", WorkbenchAudioSpendLedger)
    assert october.synthesized_characters == len(primary_plan.clips[0].text)
    assert primary_plan.token not in october.reservations
    assert primary_plan.token not in november.reservations
    persisted_plan = read_workbench_record(root, "audio_plans", primary_plan.token, WorkbenchAudioPlanRecord)
    assert persisted_plan.reservation_month == "2026-10"


def test_synthesize_workbench_audio_given_month_rollover_without_competitor_expect_reservation_transferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=100.0)
    current_month = ["2026-10"]
    monkeypatch.setattr("lesson_builder.application.operations.workbench_audio._month_key", lambda: current_month[0])
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="missing")

    def synthesize_after_rollover(text: str, voice: str) -> bytes:
        current_month[0] = "2026-11"
        return fake_audio_for_text(text, voice)

    client = FakeTtsClient.from_fn(synthesize_after_rollover)

    result = synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert result == {"completed_count": len(plan.clips), "errors": []}
    october = read_workbench_record(root, "audio_spend", "2026-10", WorkbenchAudioSpendLedger)
    november = read_workbench_record(root, "audio_spend", "2026-11", WorkbenchAudioSpendLedger)
    assert october.synthesized_characters == len(plan.clips[0].text)
    assert november.synthesized_characters == plan.characters_to_synthesize - len(plan.clips[0].text)
    assert plan.token not in october.reservations
    assert plan.token not in november.reservations
    persisted_plan = read_workbench_record(root, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
    assert persisted_plan.reservation_month == "2026-11"


def test_recover_workbench_audio_plans_given_plan_write_fails_during_month_rollover_expect_all_reservations_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=100.0)
    current_month = ["2026-10"]
    monkeypatch.setattr("lesson_builder.application.operations.workbench_audio._month_key", lambda: current_month[0])
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="missing")
    original_write = workbench_audio.write_workbench_record
    plan_write_failed = False

    def fail_once_on_rollover(workspace: Path, kind: str, identity: str, record: object) -> None:
        nonlocal plan_write_failed
        if (
            kind == "audio_plans"
            and identity == plan.token
            and getattr(record, "reservation_month", None) == "2026-11"
            and not plan_write_failed
        ):
            plan_write_failed = True
            raise OSError("plan write failed after ledger rollover")
        original_write(workspace, kind, identity, record)

    monkeypatch.setattr(workbench_audio, "write_workbench_record", fail_once_on_rollover)

    def rollover_before_provider_call(text: str, voice: str) -> bytes:
        current_month[0] = "2026-11"
        return fake_audio_for_text(text, voice)

    with pytest.raises(OSError, match="plan write failed after ledger rollover"):
        synthesize_workbench_audio(
            root, plan.token, synthesis_client=FakeTtsClient.from_fn(rollover_before_provider_call)
        )

    workbench_audio.recover_workbench_audio_plans(root, RLock())

    october = read_workbench_record(root, "audio_spend", "2026-10", WorkbenchAudioSpendLedger)
    november = read_workbench_record(root, "audio_spend", "2026-11", WorkbenchAudioSpendLedger)
    assert plan_write_failed
    assert plan.token not in october.reservations
    assert plan.token not in november.reservations


def test_synthesize_workbench_audio_given_duplicate_fingerprint_expect_findings_bound_to_selected_source(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=100.0)
    lesson_id = "greet_and_introduce_yourself"
    clips = project_workbench_audio(root, lesson_id)["clips"]
    first_by_fingerprint: dict[str, dict[str, object]] = {}
    duplicate: tuple[dict[str, object], dict[str, object]] | None = None
    for raw_clip in clips:
        clip = raw_clip
        fingerprint = str(clip["fingerprint"])
        first = first_by_fingerprint.get(fingerprint)
        if first is not None:
            duplicate = (first, clip)
            break
        first_by_fingerprint[fingerprint] = clip
    assert duplicate is not None
    selected, later = duplicate
    index = WorkbenchAudioIndex(
        clips={
            str(selected["binding"]): WorkbenchAudioIndexClip(
                fingerprint=str(selected["fingerprint"]),
                source_digest="captured-source",
                voice=str(selected["voice"]),
                audio_id=str(selected["audio_id"]),
                findings=[WorkbenchAudioCheckFinding(code="invalid_wav", message="Previous recording was invalid.")],
            )
        }
    )
    from lesson_builder.workspace.workbench_store import write_workbench_record

    write_workbench_record(root, "audio_index", lesson_id, index)
    plan = plan_workbench_audio(root, lesson_id, "missing_and_stale", selection="stale")
    assert [clip.binding for clip in plan.clips] == [str(selected["binding"])]
    assert plan.clips[0].binding != later["binding"]

    synthesize_workbench_audio(root, plan.token, synthesis_client=FakeTtsClient(b"not a wave"))

    updated = read_workbench_record(root, "audio_index", lesson_id, WorkbenchAudioIndex)
    assert str(selected["binding"]) in updated.clips
    stored_plan = read_workbench_record(root, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
    assert updated.clips[str(selected["binding"])].source_digest == stored_plan.source_digest
    assert [finding.code for finding in updated.clips[str(selected["binding"])].findings] == ["invalid_wav"]


def test_synthesize_workbench_audio_given_concurrent_invalid_clips_expect_both_checks_available_for_retry(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=100.0)
    first_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    second_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#2")
    barrier = Barrier(2)
    clients = [
        FakeTtsClient.from_fn(lambda _text, _voice: (barrier.wait(timeout=K_TEST_HANG_GUARD_SECONDS), b"not a wave")[1])
        for _ in range(2)
    ]
    lock = RLock()
    failures: list[Exception] = []

    def synthesize(plan_token: str, client: FakeTtsClient) -> None:
        try:
            synthesize_workbench_audio(root, plan_token, synthesis_client=client, lock=lock)
        except Exception as exc:
            failures.append(exc)

    threads = [
        Thread(target=synthesize, args=(first_plan.token, clients[0])),
        Thread(target=synthesize, args=(second_plan.token, clients[1])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    stale_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="stale")
    assert {clip.binding for clip in stale_plan.clips} == {first_plan.clips[0].binding, second_plan.clips[0].binding}


def test_synthesize_workbench_audio_given_provider_fails_after_invalid_wav_expect_finding_available_for_retry(
    tmp_path: Path,
) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="missing")
    calls = 0

    def fail_after_invalid_wav(text: str, voice: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return b"not a wave"
        raise RuntimeError("provider failed after first clip")

    client = FakeTtsClient.from_fn(fail_after_invalid_wav)

    with pytest.raises(RuntimeError, match="provider failed after first clip"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    stale_plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "missing_and_stale", selection="stale")
    assert [clip.binding for clip in stale_plan.clips] == [plan.clips[0].binding]


def test_synthesize_workbench_audio_given_completed_token_reused_expect_single_use_rejection(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    client = FakeTtsClient(_workbench_wav())
    synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    with pytest.raises(ValueError, match="already been used"):
        synthesize_workbench_audio(root, plan.token, synthesis_client=client)

    assert len(client.calls) == 1


def test_plan_workbench_audio_given_zero_monthly_cap_expect_blocked_estimate(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path, monthly_cap=0)

    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")

    assert (
        plan.blocked_reason
        == "This plan would exceed the monthly audio cap of $0.00; $0.00 is already spent or reserved."
    )


def test_synthesize_workbench_audio_given_invalid_wav_expect_recorded_audio_check(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    result = synthesize_workbench_audio(root, plan.token, synthesis_client=FakeTtsClient(b"not a wave"))

    assert result["completed_count"] == 1
    assert result["errors"]
    audio = project_workbench_audio(root, "greet_and_introduce_yourself")
    clip = next(item for item in audio["clips"] if item["binding"] == plan.clips[0].binding)
    assert clip["status"] == "stale"
    assert [finding["code"] for finding in clip["findings"]] == ["invalid_wav"]


def test_plan_workbench_audio_given_existing_findings_expect_stale_clip_selection(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    synthesize_workbench_audio(root, plan.token, synthesis_client=FakeTtsClient(b"invalid"))

    stale_plan = plan_workbench_audio(
        root,
        "greet_and_introduce_yourself",
        "missing_and_stale",
        selection="stale",
    )

    assert stale_plan.new_count == 1
    assert stale_plan.clips[0].binding == plan.clips[0].binding


def test_read_workbench_audio_asset_given_symlinked_cache_expect_path_rejection(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    plan = plan_workbench_audio(root, "greet_and_introduce_yourself", "block", block_key="a-first-meeting#1")
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    (root / "store" / "cache").mkdir(parents=True)
    (root / "store" / "cache" / "v4-audio").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the shared cache"):
        read_workbench_audio_asset(root, "greet_and_introduce_yourself", plan.clips[0].fingerprint)


def test_project_workbench_audio_given_invalid_voice_configuration_expect_inspection_blocker(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    (root / "content" / "authoring" / "characters.yaml").unlink()

    audio = project_workbench_audio(root, "greet_and_introduce_yourself")

    assert audio["clips"] == []
    assert audio["blockers"] == [
        "Audio unavailable: audio.character_voice_overrides references unknown character_id 'amina'"
    ]


def test_project_workbench_audio_given_voice_changed_since_release_expect_voice_warning(tmp_path: Path) -> None:
    root = _audio_workspace(tmp_path)
    lesson_id = "greet_and_introduce_yourself"
    current = project_workbench_audio(root, lesson_id)
    speaker_id = current["voices"][0]["speaker_id"]
    record_release_audio_voices(root, "v2026.10.01", {lesson_id: {speaker_id: "previous-release-voice"}})

    updated = project_workbench_audio(root, lesson_id)
    voice = next(item for item in updated["voices"] if item["speaker_id"] == speaker_id)

    assert voice["changed_since_release"] is True
    assert voice["released_voice"] == "previous-release-voice"


def test_inspect_workbench_audio_bytes_given_silent_recording_expect_silent_finding() -> None:
    result = inspect_workbench_audio_bytes(_wav(samples=array("h", [0] * 24_000)), text="Hei!")

    assert [finding.code for finding in result.findings] == ["silent"]


def test_inspect_workbench_audio_given_truncated_pcm_expect_invalid_wav_finding() -> None:
    complete = _wav(samples=array("h", [1000] * 24_000))
    truncated = complete[: 44 + 2_400]

    result = inspect_workbench_audio_bytes(truncated, text="Hei!")

    assert result.duration_ms is None
    assert [finding.code for finding in result.findings] == ["invalid_wav"]


def test_inspect_workbench_audio_bytes_given_short_recording_expect_length_finding() -> None:
    result = inspect_workbench_audio_bytes(_wav(samples=array("h", [1000] * 240)), text="Hei, hyggelig å møte deg.")

    assert [finding.code for finding in result.findings] == ["length"]


def test_inspect_workbench_audio_bytes_given_loud_final_waveform_expect_cut_off_finding() -> None:
    result = inspect_workbench_audio_bytes(_wav(samples=array("h", [9000] * 24_000)), text="Hei!")

    assert [finding.code for finding in result.findings] == ["cut_off"]


def _audio_workspace(tmp_path: Path, *, monthly_cap: float = 5.0) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    root = tmp_path / "workspace"
    root.mkdir()
    source = repo_root / "content" / "lessons" / "greet_and_introduce_yourself"
    target = root / "content" / "lessons" / source.name
    target.parent.mkdir(parents=True)
    shutil.copytree(source, target)
    authoring = root / "content" / "authoring"
    authoring.mkdir(parents=True)
    shutil.copy2(repo_root / "content" / "authoring" / "characters.yaml", authoring / "characters.yaml")
    settings = default_workbench_settings(root).model_copy(
        update={"audio": WorkbenchAudioSettings(monthly_cap_usd=monthly_cap)}
    )
    write_workbench_settings(root, settings)
    shutil.copy2(repo_root / "config.yaml", root / "config.yaml")
    return root


def _workbench_wav() -> bytes:
    samples = array("h", [10_000] * (24_000 - 720) + [0] * 720)
    return _wav(samples=samples)


def _wav(*, samples: array) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()
