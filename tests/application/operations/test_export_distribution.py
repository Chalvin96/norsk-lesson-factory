"""Entry point: `export_distribution` and `validate_distribution` behavior."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import wave
from io import BytesIO
from pathlib import Path

import pytest

from lesson_builder.application.operations import export_distribution as distribution_operation
from lesson_builder.application.operations.export_distribution import create_lesson_packet_from_source
from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.synthesize_audio import AudioSettings
from lesson_builder.application.operations.validate_distribution import validate_committed_distribution
from lesson_builder.application.operations.validate_distribution import validate_distribution
from lesson_builder.domain.lesson.models.characters import CharacterDefinition
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.models.export import ExportedLesson
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT

K_LESSON_FILES = ("plan.md", "lesson.md", "exercises.yaml")


def test_export_distribution_given_curriculum_slot_order_expect_required_catalog_preserves_plan(
    tmp_path: Path,
):
    _copy_package(tmp_path, "alpha_dialogue")
    _copy_package(tmp_path, "zulu_travel")
    _write_plan(tmp_path, ["zulu_travel", "alpha_dialogue"])

    result = export_distribution(tmp_path)

    catalog = _catalog(tmp_path)
    assert catalog == {
        "lessons": [
            {"lesson_id": "zulu_travel", "position": 0},
            {"lesson_id": "alpha_dialogue", "position": 1},
        ]
    }
    assert result["lesson_ids"] == ["zulu_travel", "alpha_dialogue"]
    assert result["created"] == 2
    assert result["lesson_count"] == 2
    assert result["validation"]["lesson_count"] == 2
    assert not (tmp_path / "dist" / "manifest.json").exists()


def test_export_distribution_given_backup_only_and_build_failure_expect_old_dist_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_package(tmp_path, "question_word_order")
    _write_plan(tmp_path, ["question_word_order"])
    export_distribution(tmp_path)
    dist = tmp_path / "dist"
    backup = tmp_path / ".dist.backup"
    dist.rename(backup)

    def fail_export(*args: object, **kwargs: object) -> object:
        raise RuntimeError("build failed")

    monkeypatch.setattr(distribution_operation, "create_lesson_packet_from_source", fail_export)
    with pytest.raises(RuntimeError, match="build failed"):
        export_distribution(tmp_path)
    assert dist.is_dir()
    assert not backup.exists()


def test_export_distribution_given_valid_current_and_backup_expect_backup_cleanup(tmp_path: Path) -> None:
    _copy_package(tmp_path, "question_word_order")
    _write_plan(tmp_path, ["question_word_order"])
    export_distribution(tmp_path)
    backup = tmp_path / ".dist.backup"
    shutil.copytree(tmp_path / "dist", backup)
    export_distribution(tmp_path)
    assert (tmp_path / "dist").is_dir()
    assert not backup.exists()


def test_export_distribution_given_invalid_current_and_valid_backup_expect_both_preserved(tmp_path: Path) -> None:
    _copy_package(tmp_path, "question_word_order")
    _write_plan(tmp_path, ["question_word_order"])
    export_distribution(tmp_path)
    backup = tmp_path / ".dist.backup"
    shutil.copytree(tmp_path / "dist", backup)
    (tmp_path / "dist" / "catalog.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="current dist is invalid"):
        export_distribution(tmp_path)
    assert (tmp_path / "dist").is_dir()
    assert backup.is_dir()


def test_export_distribution_given_ambiguous_backup_expect_refusal(tmp_path: Path) -> None:
    _copy_package(tmp_path, "question_word_order")
    _write_plan(tmp_path, ["question_word_order"])
    export_distribution(tmp_path)
    ambiguous = tmp_path / ".dist.backup-legacy"
    ambiguous.mkdir()
    with pytest.raises(RuntimeError, match="unrecognized interrupted"):
        export_distribution(tmp_path)
    assert ambiguous.is_dir()


def test_export_distribution_given_missing_planned_package_expect_error_before_publication(tmp_path: Path):
    _copy_package(tmp_path, "alpha_dialogue")
    _write_plan(tmp_path, ["alpha_dialogue"])
    export_distribution(tmp_path)
    catalog_before = (tmp_path / "dist" / "catalog.json").read_bytes()
    _write_plan(tmp_path, ["alpha_dialogue", "ghost_lesson"])

    with pytest.raises(ValueError, match="ghost_lesson"):
        export_distribution(tmp_path)

    assert (tmp_path / "dist" / "catalog.json").read_bytes() == catalog_before
    assert not _packet_path(tmp_path, "ghost_lesson").exists()


def test_export_distribution_given_extra_complete_package_expect_error_before_publication(tmp_path: Path):
    _copy_package(tmp_path, "alpha_dialogue")
    _copy_package(tmp_path, "zulu_travel")
    _write_plan(tmp_path, ["alpha_dialogue"])

    with pytest.raises(ValueError, match="zulu_travel"):
        export_distribution(tmp_path)

    assert not (tmp_path / "dist").exists()


def test_export_distribution_given_incomplete_package_dir_expect_error_not_skipped(tmp_path: Path):
    _copy_package(tmp_path, "alpha_dialogue")
    _write_plan(tmp_path, ["alpha_dialogue"])
    broken = tmp_path / "content" / "lessons" / "broken_lesson"
    broken.mkdir()
    (broken / "plan.md").write_text("stub", encoding="utf-8")

    with pytest.raises(ValueError, match="broken_lesson"):
        export_distribution(tmp_path)


def test_export_distribution_given_removed_source_lesson_expect_stale_packet_and_audio_removed(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    assert _packet_path(tmp_path, "zulu_travel").is_file()
    stray_audio = tmp_path / "dist" / "audio" / "alpha_dialogue" / "stale.wav"
    stray_audio.parent.mkdir(parents=True)
    stray_audio.write_bytes(b"stale audio")

    shutil.rmtree(tmp_path / "content" / "lessons" / "zulu_travel")
    _write_plan(tmp_path, ["alpha_dialogue"])
    result = export_distribution(tmp_path)

    lessons_dir = tmp_path / "dist" / "lessons"
    assert sorted(path.name for path in lessons_dir.glob("*.json")) == ["alpha_dialogue.json"]
    assert not _packet_path(tmp_path, "zulu_travel").exists()
    assert not stray_audio.exists()
    assert _catalog(tmp_path) == {"lessons": [{"lesson_id": "alpha_dialogue", "position": 0}]}
    assert result["unchanged"] == 1
    assert result["created"] == 0
    assert result["updated"] == 0


def test_export_distribution_given_schema_write_failure_expect_previous_distribution_intact(
    tmp_path: Path,
    monkeypatch,
):
    _assemble_two_lesson_repo(tmp_path)
    dist_before = {
        path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / "dist").rglob("*") if path.is_file()
    }

    def fail_schema(path: Path) -> Path:
        raise OSError("schema disk failure")

    monkeypatch.setattr(distribution_operation, "write_export_json_schema", fail_schema)
    with pytest.raises(OSError, match="schema disk failure"):
        export_distribution(tmp_path)

    dist_after = {
        path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / "dist").rglob("*") if path.is_file()
    }
    assert dist_after == dist_before
    assert not list(tmp_path.glob(".distribution-export-*"))


def test_validate_distribution_given_complete_release_expect_summary(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)

    summary = validate_distribution(tmp_path, expected_lesson_ids=["zulu_travel", "alpha_dialogue"])

    assert summary["lesson_count"] == 2
    assert summary["packet_count"] == 2
    assert summary["lesson_ids"] == ["zulu_travel", "alpha_dialogue"]
    assert summary["audio_file_count"] == 0
    assert summary["audio_total_bytes"] == 0
    assert summary["schema_path"] == str(tmp_path / "dist" / "schema" / "lesson.schema.json")


def test_validate_distribution_given_manifest_absent_expect_packets_validate_with_catalog(tmp_path: Path):
    _copy_package(tmp_path, "question_word_order")
    _write_plan(tmp_path, ["question_word_order"])
    export_distribution(tmp_path)

    packet_path = _packet_path(tmp_path, "question_word_order")
    ExportedLesson.model_validate_json(packet_path.read_text(encoding="utf-8"))
    summary = validate_distribution(tmp_path, expected_lesson_ids=["question_word_order"])

    assert not (tmp_path / "dist" / "manifest.json").exists()
    assert summary["catalog_present"] is True
    assert summary["lesson_ids"] == ["question_word_order"]


def test_validate_distribution_given_unexpected_lesson_order_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)

    with pytest.raises(ValueError, match="order"):
        validate_distribution(tmp_path, expected_lesson_ids=["alpha_dialogue", "zulu_travel"])


def test_validate_distribution_given_catalog_packet_metadata_expect_rejection(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    catalog_path = tmp_path / "dist" / "catalog.json"
    catalog = _catalog(tmp_path)
    catalog["lessons"][0]["title"] = "must stay in the packet"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="packet or factory metadata"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_catalog_factory_run_metadata_expect_rejection(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    catalog_path = tmp_path / "dist" / "catalog.json"
    catalog = _catalog(tmp_path)
    catalog["factory_run_id"] = "run-1"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="packet or factory metadata"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_undeclared_packet_file_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    _packet_path(tmp_path, "rogue_lesson").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="catalog lesson inventory"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_missing_declared_packet_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    _packet_path(tmp_path, "alpha_dialogue").unlink()

    with pytest.raises(ValueError, match="catalog lesson inventory"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_invalid_packet_json_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    _packet_path(tmp_path, "alpha_dialogue").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ValueError, match="public export schema"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_missing_catalog_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    (tmp_path / "dist" / "catalog.json").unlink()

    with pytest.raises(ValueError, match="catalog not found"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_missing_audio_file_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    _rewrite_packet_audio(
        tmp_path,
        "alpha_dialogue",
        {
            "id": "audio-t-lesson-s1-reading-a1",
            "path": f"audio/lessons/alpha_dialogue/{_audio_uuid(1)}.wav",
            "url": f"https://media.example/audio/lessons/alpha_dialogue/{_audio_uuid(1)}.wav",
            "mime": "audio/wav",
            "status": "synthesized",
        },
    )

    with pytest.raises(ValueError, match="missing audio"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_audio_hash_drift_expect_error_after_valid_distribution(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    audio_bytes = _wav_bytes()
    audio_file = tmp_path / "dist" / "audio" / "lessons" / "alpha_dialogue" / f"{_audio_uuid(2)}.wav"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(audio_bytes)
    _rewrite_packet_audio(
        tmp_path,
        "alpha_dialogue",
        {
            "id": "audio-clip",
            "path": f"audio/lessons/alpha_dialogue/{_audio_uuid(2)}.wav",
            "url": f"https://media.example/audio/lessons/alpha_dialogue/{_audio_uuid(2)}.wav",
            "mime": "audio/wav",
            "status": "synthesized",
            "sha256": hashlib.sha256(audio_bytes).hexdigest(),
        },
    )
    assert validate_distribution(tmp_path)["audio_file_count"] == 1

    audio_file.write_bytes(_wav_bytes(frames=480))

    with pytest.raises(ValueError, match="sha256"):
        validate_distribution(tmp_path)


def test_export_distribution_given_real_audio_expect_complete_curriculum_distribution_and_cache_reuse(
    tmp_path: Path,
):
    _copy_package(tmp_path, "alpha_dialogue")
    _copy_package(tmp_path, "zulu_travel")
    _write_plan(tmp_path, ["zulu_travel", "alpha_dialogue"])
    synthesis_client = RecordingSynthesisClient()
    cache = tmp_path / "cache"
    first_distribution = tmp_path / "distribution-one"
    second_distribution = tmp_path / "distribution-two"

    first = export_distribution(
        tmp_path,
        output_root=first_distribution,
        audio_settings=_audio_settings(tmp_path),
        synthesis_client=synthesis_client,
        audio_cache_root=cache,
        audio_public_base_url="https://media.example",
    )
    first_call_count = len(synthesis_client.calls)
    second = export_distribution(
        tmp_path,
        output_root=second_distribution,
        audio_settings=_audio_settings(tmp_path),
        synthesis_client=synthesis_client,
        audio_cache_root=cache,
        audio_public_base_url="https://media.example",
    )

    assert first["audio_mode"] is True
    assert second["audio_mode"] is True
    assert first_call_count > 0
    assert len(synthesis_client.calls) == first_call_count
    assert first["validation"]["audio_file_count"] > 0
    packet = json.loads(_packet_path(first_distribution, "zulu_travel").read_text(encoding="utf-8"))
    for audio in packet["media"]["audio"]:
        path = Path(audio["path"])
        assert path.parts[:3] == ("audio", "lessons", "zulu_travel")
        uuid.UUID(path.stem)
        assert audio["url"] == f"https://media.example/{audio['path']}"
    assert _catalog(second_distribution)["lessons"] == [
        {"lesson_id": "zulu_travel", "position": 0},
        {"lesson_id": "alpha_dialogue", "position": 1},
    ]


def test_export_distribution_given_character_mapping_expect_public_packets_unchanged_and_voice_reused(tmp_path: Path):
    alpha = _copy_package(tmp_path, "alpha_dialogue")
    zulu = _copy_package(tmp_path, "zulu_travel")
    _append_character_reading(alpha, speaker_id="local-alpha")
    _append_character_reading(zulu, speaker_id="local-zulu")
    _write_plan(tmp_path, ["zulu_travel", "alpha_dialogue"])

    provider_free_root = tmp_path / "provider-free"
    export_distribution(tmp_path, output_root=provider_free_root)

    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=tmp_path / "service-account.json",
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B",),
        character_registry=CharacterRegistry(
            characters={"anna": CharacterDefinition(character_id="anna", name="Anna")}
        ),
        character_voice_overrides={"anna": "nb-NO-Wavenet-C"},
    )
    settings.service_account_path.write_text("{}", encoding="utf-8")
    synthesis_client = RecordingSynthesisClient()
    audio_root = tmp_path / "with-audio"
    result = export_distribution(
        tmp_path,
        output_root=audio_root,
        audio_settings=settings,
        synthesis_client=synthesis_client,
        audio_cache_root=tmp_path / "cache",
        audio_public_base_url="https://media.example",
    )

    # The second lesson is a cache hit because the same mapped character and
    # sentence produce the same synthesis fingerprint across lesson-local IDs.
    anna_voices = [voice for text, voice in synthesis_client.calls if text == "Hei, Anna."]
    assert anna_voices == ["nb-NO-Wavenet-C"]
    assert result["validation"]["lesson_ids"] == ["zulu_travel", "alpha_dialogue"]
    for lesson_id in ("zulu_travel", "alpha_dialogue"):
        provider_free_packet = json.loads(_packet_path(provider_free_root, lesson_id).read_text(encoding="utf-8"))
        audio_packet = json.loads(_packet_path(audio_root, lesson_id).read_text(encoding="utf-8"))
        assert _without_audio_references(audio_packet) == _without_audio_references(provider_free_packet)
        serialized = json.dumps(audio_packet, ensure_ascii=False)
        assert "character_id" not in serialized
        assert "nb-NO-Wavenet-C" not in serialized
    assert "character_id" not in json.dumps(_catalog(audio_root), ensure_ascii=False)


def test_export_distribution_given_profile_aware_cast_expect_compatible_private_voices(tmp_path: Path):
    alpha = _copy_package(tmp_path, "alpha_dialogue")
    zulu = _copy_package(tmp_path, "zulu_travel")
    _append_dialogue_readings(
        alpha,
        [
            ("mina-local", "Mina", 'character_id="mina"'),
            ("erik", "Erik", 'voice_profile="masculine"'),
        ],
    )
    _append_dialogue_readings(
        zulu,
        [("mina-other-local", "Mina", 'character_id="mina"')],
    )
    _write_plan(tmp_path, ["zulu_travel", "alpha_dialogue"])

    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Chirp3-HD-Zephyr",
        speaker_voice_pool=(
            "nb-NO-Chirp3-HD-Leda",
            "nb-NO-Chirp3-HD-Alnilam",
            "nb-NO-Chirp3-HD-Aoede",
            "nb-NO-Chirp3-HD-Charon",
        ),
        speaker_voice_overrides={"teacher": "nb-NO-Chirp3-HD-Gacrux"},
        character_registry=CharacterRegistry(
            characters={"mina": CharacterDefinition(character_id="mina", name="Mina", voice_profile="feminine")}
        ),
        character_voice_overrides={"mina": "nb-NO-Chirp3-HD-Leda"},
        voice_profiles={
            "nb-NO-Chirp3-HD-Zephyr": "feminine",
            "nb-NO-Chirp3-HD-Leda": "feminine",
            "nb-NO-Chirp3-HD-Aoede": "feminine",
            "nb-NO-Chirp3-HD-Gacrux": "feminine",
            "nb-NO-Chirp3-HD-Alnilam": "masculine",
            "nb-NO-Chirp3-HD-Charon": "masculine",
        },
    )
    synthesis_client = RecordingSynthesisClient()
    audio_root = tmp_path / "with-audio"
    export_distribution(
        tmp_path,
        output_root=audio_root,
        audio_settings=settings,
        synthesis_client=synthesis_client,
        audio_cache_root=tmp_path / "cache",
        audio_public_base_url="https://media.example",
    )
    voices = dict(synthesis_client.calls)

    # zulu_travel synthesizes first: the pinned feminine Mina keeps her voice
    # across local speaker IDs, and Erik draws a masculine pool voice even
    # though the first pool slot is feminine.
    assert voices["Hei, Mina."] == "nb-NO-Chirp3-HD-Leda"
    assert voices["Hei, Erik."] == "nb-NO-Chirp3-HD-Alnilam"
    serialized_release = "\n".join(
        _packet_path(audio_root, lesson_id).read_text(encoding="utf-8")
        for lesson_id in ("zulu_travel", "alpha_dialogue")
    )
    assert "voice_profile" not in serialized_release
    assert "character_id" not in serialized_release
    assert "nb-NO-Chirp3-HD-Alnilam" not in serialized_release
    assert "nb-NO-Chirp3-HD-Leda" not in serialized_release


def test_export_distribution_given_later_audio_failure_expect_previous_distribution_unchanged(tmp_path: Path):
    _copy_package(tmp_path, "alpha_dialogue")
    _copy_package(tmp_path, "zulu_travel")
    _write_plan(tmp_path, ["alpha_dialogue", "zulu_travel"])
    distribution_root = tmp_path / "distribution"
    export_distribution(tmp_path, output_root=distribution_root)
    before = {
        path.relative_to(distribution_root): path.read_bytes()
        for path in (distribution_root / "dist").rglob("*")
        if path.is_file()
    }
    synthesis_client = RecordingSynthesisClient(fail_after=1)

    with pytest.raises(RuntimeError, match="provider failed later"):
        export_distribution(
            tmp_path,
            output_root=distribution_root,
            audio_settings=_audio_settings(tmp_path),
            synthesis_client=synthesis_client,
            audio_cache_root=tmp_path / "cache",
            audio_public_base_url="https://media.example",
        )

    after = {
        path.relative_to(distribution_root): path.read_bytes()
        for path in (distribution_root / "dist").rglob("*")
        if path.is_file()
    }
    assert after == before


def test_validate_distribution_given_unreferenced_audio_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    stale = tmp_path / "dist" / "audio" / "lessons" / "alpha_dialogue" / "stale.wav"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(_wav_bytes())

    with pytest.raises(ValueError, match="unreferenced audio"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_corrupt_wav_with_matching_hash_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    corrupt = b"not a wav"
    audio_file = tmp_path / "dist" / "audio" / "lessons" / "alpha_dialogue" / f"{_audio_uuid(3)}.wav"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(corrupt)
    _rewrite_packet_audio(
        tmp_path,
        "alpha_dialogue",
        {
            "id": "audio-clip",
            "path": f"audio/lessons/alpha_dialogue/{_audio_uuid(3)}.wav",
            "url": f"https://media.example/audio/lessons/alpha_dialogue/{_audio_uuid(3)}.wav",
            "mime": "audio/wav",
            "status": "synthesized",
            "sha256": hashlib.sha256(corrupt).hexdigest(),
        },
    )

    with pytest.raises(ValueError, match="readable WAV"):
        validate_distribution(tmp_path)


def test_validate_distribution_given_wrong_schema_expect_error(tmp_path: Path):
    _assemble_two_lesson_repo(tmp_path)
    schema_path = tmp_path / "dist" / "schema" / "lesson.schema.json"
    schema_path.write_text('{"type": "object"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="current public export schema"):
        validate_distribution(tmp_path)


def test_validate_committed_distribution_given_source_changed_after_export_expect_stale_error(
    tmp_path: Path,
):
    _assemble_two_lesson_repo(tmp_path)
    lesson_path = tmp_path / "content" / "lessons" / "alpha_dialogue" / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace("What you will learn in this lesson", "Fresh overview"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="stale against canonical source"):
        validate_committed_distribution(tmp_path)


def test_create_lesson_packet_from_source_given_nested_package_expect_parent_registry_projection(tmp_path: Path):
    package = _copy_package(tmp_path, "alpha_dialogue")
    _append_character_reading(package, speaker_id="local-alpha")
    registry = CharacterRegistry(
        characters={"anna": CharacterDefinition(character_id="anna", name="Anna", voice_profile="feminine")}
    )
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  anna:\n    name: Anna\n    voice_profile: feminine\n",
        encoding="utf-8",
    )

    packet, _metadata = create_lesson_packet_from_source(package)
    expected, _metadata = create_lesson_packet_from_source(package, character_registry=registry)

    assert packet == expected


def test_create_lesson_packet_from_source_given_unknown_character_expect_validation_error(tmp_path: Path):
    package = _copy_package(tmp_path, "alpha_dialogue")
    _append_character_reading(package, speaker_id="local-alpha")
    lesson_path = package / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace('character_id="anna"', 'character_id="unknown"'),
        encoding="utf-8",
    )
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  anna:\n    name: Anna\n    voice_profile: feminine\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown recurring character_id 'unknown'"):
        create_lesson_packet_from_source(package)


def _copy_package(repo: Path, lesson_id: str) -> Path:
    package = repo / "content" / "lessons" / lesson_id
    package.mkdir(parents=True)
    for name in K_LESSON_FILES:
        source = (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        source = source.replace("question_word_order", lesson_id)
        (package / name).write_text(source, encoding="utf-8")
    return package


def _write_plan(repo: Path, lesson_ids: list[str]) -> None:
    slots = "".join(
        f"- sequence_index: {index}\n"
        f"  catalog_id: {lesson_id}\n"
        f"  catalog_kind: grammar\n"
        f"  title: {lesson_id}\n"
        f"  cefr_level: A1\n"
        f"  human_gate: approved\n"
        for index, lesson_id in enumerate(lesson_ids)
    )
    plan_path = repo / "content" / "curriculum" / "plan.yaml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(f"schema_version: 0.5-catalog-plan\nslots:\n{slots}", encoding="utf-8")


def _assemble_two_lesson_repo(repo: Path) -> None:
    _copy_package(repo, "alpha_dialogue")
    _copy_package(repo, "zulu_travel")
    _write_plan(repo, ["zulu_travel", "alpha_dialogue"])
    export_distribution(repo)


def _append_character_reading(package: Path, *, speaker_id: str) -> None:
    lesson_path = package / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8")
        + (
            '\n::: {.reading translation="Hello, Anna." dialogue_id="d1" '
            f'speaker_id="{speaker_id}" speaker_name="Anna" character_id="anna"}}\n'
            "Hei, Anna.\n"
            ":::\n"
        ),
        encoding="utf-8",
    )


def _append_dialogue_readings(package: Path, turns: list[tuple[str, str, str]]) -> None:
    """Append authored dialogue turns as (speaker_id, name, extra attributes)."""
    lesson_path = package / "lesson.md"
    additions = ""
    for speaker_id, speaker_name, extra in turns:
        first_name = speaker_name.split()[0].lower()
        additions += (
            f'\n::: {{.reading translation="Hello, {speaker_name}." dialogue_id="d1" '
            f'speaker_id="{speaker_id}" speaker_name="{speaker_name}" {extra}}}\n'
            f"Hei, {first_name.title()}.\n"
            ":::\n"
        )
    lesson_path.write_text(lesson_path.read_text(encoding="utf-8") + additions, encoding="utf-8")


def _without_audio_references(payload: object) -> object:
    if isinstance(payload, list):
        return [_without_audio_references(value) for value in payload]
    if not isinstance(payload, dict):
        return payload
    result = {key: _without_audio_references(value) for key, value in payload.items() if key != "audio_id"}
    media = result.get("media")
    if isinstance(media, dict):
        media["audio"] = []
    return result


def _catalog(repo: Path) -> dict:
    return json.loads((repo / "dist" / "catalog.json").read_text(encoding="utf-8"))


def _packet_path(repo: Path, lesson_id: str) -> Path:
    return repo / "dist" / "lessons" / f"{lesson_id}.json"


def _rewrite_packet_audio(repo: Path, lesson_id: str, audio_entry: dict) -> None:
    packet_path = _packet_path(repo, lesson_id)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["media"]["audio"] = [audio_entry]
    attached = False
    for section in packet["sections"]:
        for block in section["blocks"]:
            if block["kind"] in {"reading", "example"}:
                block["audio_id"] = audio_entry["id"]
                attached = True
                break
            if block["kind"] == "examples" and block["items"]:
                block["items"][0]["audio_id"] = audio_entry["id"]
                attached = True
                break
        if attached:
            break
    assert attached
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wav_bytes(*, frames: int = 240) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _audio_uuid(index: int) -> str:
    """Return a deterministic UUID-shaped audio object name for a fixture."""
    return f"00000000-0000-5000-8000-{index:012x}"


class RecordingSynthesisClient:
    """Behavior fake for the injected synthesis seam: records calls, returns WAV bytes."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail_after = fail_after

    def synthesize(self, *, text: str, voice: str) -> bytes:
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise RuntimeError("provider failed later")
        self.calls.append((text, voice))
        return _wav_bytes()


def _audio_settings(tmp_path: Path) -> AudioSettings:
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    return AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
    )
