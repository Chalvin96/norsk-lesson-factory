"""Entry point: `synthesize_lesson_audio` orchestration over an injected synthesis client.

Provider request, token, and retry behavior live with the Google TTS adapter
tests under ``tests/clients/tts``; these tests exercise distribution orchestration:
voice allocation, cache identity, WAV validation, and the public inventory.
"""

from __future__ import annotations

import json
import wave
from io import BytesIO
from pathlib import Path

import pytest

from lesson_builder.application.operations.synthesize_audio import AudioSettings
from lesson_builder.application.operations.synthesize_audio import load_audio_settings
from lesson_builder.application.operations.synthesize_audio import synthesize_lesson_audio
from lesson_builder.domain.lesson.models.characters import CharacterDefinition
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.services.audio_bindings import audio_binding_key
from tests.application.fakes import FakeSynthesisClient


def test_audio_binding_key_given_example_source_expect_stable_locator():
    assert audio_binding_key({"section_id": "s1", "block_index": 2, "item_index": 1}) == ("section:s1:block:2:item:1")


def test_load_audio_settings_given_pool_and_overrides_expect_ordered_private_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account, public_base_url="https://media.example/")
    (tmp_path / "config.yaml").write_text(
        "audio:\n"
        "  language: nb-NO\n"
        "  voice: nb-NO-Wavenet-A\n"
        "  speaker_voice_pool:\n"
        "    - nb-NO-Wavenet-B\n"
        "    - nb-NO-Wavenet-C\n"
        "  speaker_voice_overrides:\n"
        "    teacher: nb-NO-Wavenet-D\n",
        encoding="utf-8",
    )

    settings = load_audio_settings(repo_root=tmp_path)

    assert settings.language == "nb-NO"
    assert settings.service_account_path == account
    assert settings.public_base_url == "https://media.example"
    assert settings.speaker_voice_pool == ("nb-NO-Wavenet-B", "nb-NO-Wavenet-C")
    assert settings.speaker_voice_overrides == {"teacher": "nb-NO-Wavenet-D"}


def test_load_audio_settings_given_character_mapping_expect_private_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  anna:\n    name: Anna\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "audio:\n  character_voice_overrides:\n    anna: nb-NO-Chirp3-HD-Leda\n  synthesis_revision: cast-v2\n",
        encoding="utf-8",
    )

    settings = load_audio_settings(repo_root=tmp_path)

    assert settings.character_voice_overrides == {"anna": "nb-NO-Chirp3-HD-Leda"}
    assert settings.synthesis_revision == "cast-v2"
    assert settings.character_registry.characters["anna"].name == "Anna"


def test_load_audio_settings_given_voice_profiles_expect_private_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    (tmp_path / "config.yaml").write_text(
        "audio:\n"
        "  voice_profiles:\n"
        "    feminine:\n"
        "      - nb-NO-Chirp3-HD-Leda\n"
        "    masculine:\n"
        "      - nb-NO-Chirp3-HD-Alnilam\n",
        encoding="utf-8",
    )

    settings = load_audio_settings(repo_root=tmp_path)

    assert settings.voice_profiles == {
        "nb-NO-Chirp3-HD-Leda": "feminine",
        "nb-NO-Chirp3-HD-Alnilam": "masculine",
    }


def test_load_audio_settings_given_unknown_profile_name_expect_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    (tmp_path / "config.yaml").write_text(
        "audio:\n  voice_profiles:\n    robotic:\n      - nb-NO-Chirp3-HD-Puck\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="authored vocabulary"):
        load_audio_settings(repo_root=tmp_path)


def test_load_audio_settings_given_conflicting_voice_classification_expect_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    (tmp_path / "config.yaml").write_text(
        "audio:\n"
        "  voice_profiles:\n"
        "    feminine:\n"
        "      - nb-NO-Chirp3-HD-Leda\n"
        "    masculine:\n"
        "      - nb-NO-Chirp3-HD-Leda\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="classified as both"):
        load_audio_settings(repo_root=tmp_path)


def test_load_audio_settings_given_character_pin_profile_conflict_expect_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  jonas:\n    name: Jonas\n    voice_profile: masculine\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "audio:\n"
        "  voice_profiles:\n"
        "    feminine:\n"
        "      - nb-NO-Chirp3-HD-Leda\n"
        "  character_voice_overrides:\n"
        "    jonas: nb-NO-Chirp3-HD-Leda\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pinned voice"):
        load_audio_settings(repo_root=tmp_path)


def test_load_audio_settings_given_unknown_character_mapping_expect_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("schema_version: 1\ncharacters: {}\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "audio:\n  character_voice_overrides:\n    anna: nb-NO-Chirp3-HD-Leda\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown character_id"):
        load_audio_settings(repo_root=tmp_path)


def test_load_audio_settings_given_malformed_pool_expect_type_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    account = tmp_path / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    _set_audio_env(monkeypatch, account)
    (tmp_path / "config.yaml").write_text(
        "audio:\n  speaker_voice_pool: not-a-list\n",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="speaker_voice_pool"):
        load_audio_settings(repo_root=tmp_path)


def test_synthesize_lesson_audio_given_reading_candidate_expect_wav_entry_and_binding(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-lesson-s1-reading-a1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0},
        },
        {
            "id": "t-exercise-e1-target",
            "text": "Hei.",
            "origin": "exercise_target",
            "source": {"exercise_id": "e1"},
        },
    ]

    entries, bindings = synthesize_lesson_audio(
        lesson_id="present_tense",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert len(entries) == 1
    assert entries[0]["path"].startswith("audio/lessons/present_tense/")
    assert entries[0]["path"].endswith(".wav")
    assert bindings["section:s1:block:0"] == entries[0]["id"]
    assert bindings["exercise:e1"] == entries[0]["id"]
    asset = tmp_path / "dist" / entries[0]["path"]
    assert asset.is_file()
    assert len(client.calls) == 1


def test_synthesize_lesson_audio_given_repeated_speakers_expect_stable_distinct_allocation(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B", "nb-NO-Wavenet-C"),
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-anna",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0, "speaker_id": "anna"},
        },
        {
            "id": "t-bo",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 1, "speaker_id": "bo"},
        },
        {
            "id": "t-anna-again",
            "text": "Ja.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 2, "speaker_id": "anna"},
        },
    ]

    entries, _bindings = synthesize_lesson_audio(
        lesson_id="voice-selection",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )
    first_pass = _dialogue_voice_names(client)

    assert first_pass == ["nb-NO-Wavenet-B", "nb-NO-Wavenet-C", "nb-NO-Wavenet-B"]
    assert len(entries) == 3

    repeat_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="voice-selection",
        transcript_blocks=blocks,
        distribution_root=tmp_path / "repeat",
        settings=settings,
        synthesis_client=repeat_client,
    )

    assert _dialogue_voice_names(repeat_client) == first_pass


def test_synthesize_lesson_audio_given_recurring_character_expect_same_voice_across_lessons(
    tmp_path: Path, monkeypatch
):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B",),
        character_registry=CharacterRegistry(
            characters={"anna": CharacterDefinition(character_id="anna", name="Anna")}
        ),
        character_voice_overrides={"anna": "nb-NO-Wavenet-C"},
    )
    blocks = [
        {
            "id": "t-anna-1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {
                "section_id": "s1",
                "block_index": 0,
                "speaker_id": "speaker-a",
                "speaker_name": "Anna",
                "dialogue_id": "d1",
                "character_id": "anna",
            },
        }
    ]

    first_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="first-lesson",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=first_client,
    )
    second_client = _synthesis_client()
    second_blocks = [{**blocks[0], "source": {**blocks[0]["source"], "speaker_id": "speaker-b"}}]
    synthesize_lesson_audio(
        lesson_id="second-lesson",
        transcript_blocks=second_blocks,
        distribution_root=tmp_path / "second",
        settings=settings,
        synthesis_client=second_client,
    )

    assert _dialogue_voice_names(first_client) == ["nb-NO-Wavenet-C"]
    assert _dialogue_voice_names(second_client) == ["nb-NO-Wavenet-C"]


def test_synthesize_lesson_audio_given_unresolved_character_expect_no_provider_call(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        character_registry=CharacterRegistry(characters={}),
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-unknown",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {
                "section_id": "s1",
                "block_index": 0,
                "speaker_id": "speaker-a",
                "speaker_name": "Anna",
                "dialogue_id": "d1",
                "character_id": "anna",
            },
        }
    ]

    with pytest.raises(ValueError, match="unknown recurring character_id"):
        synthesize_lesson_audio(
            lesson_id="unresolved-character",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )


def test_synthesize_lesson_audio_given_interleaved_profiled_speakers_expect_compatible_distinct_voices(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(tmp_path)
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-jonas",
            "text": "Jeg lager kaffe nå.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "jonas", voice_profile="masculine"),
        },
        {
            "id": "t-mina",
            "text": "Jeg jobber hjemme i dag.",
            "origin": "lesson_reading",
            "source": _dialogue_source(1, "mina", voice_profile="feminine"),
        },
    ]

    synthesize_lesson_audio(
        lesson_id="present-tense",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert _dialogue_voice_names(client) == [
        "nb-NO-Chirp3-HD-Alnilam",
        "nb-NO-Chirp3-HD-Leda",
    ]


def test_synthesize_lesson_audio_given_profiled_one_offs_expect_subset_exhaustion_reuse(tmp_path: Path, monkeypatch):
    settings = _chirp_settings(tmp_path)
    speakers = ("erik", "ola", "per")
    blocks = [
        {
            "id": f"t-{speaker}",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(index, speaker, voice_profile="masculine"),
        }
        for index, speaker in enumerate(speakers)
    ]
    client = _synthesis_client()

    synthesize_lesson_audio(
        lesson_id="masculine-subset",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )
    first_pass = _dialogue_voice_names(client)

    repeat_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="masculine-subset",
        transcript_blocks=blocks,
        distribution_root=tmp_path / "repeat",
        settings=settings,
        synthesis_client=repeat_client,
    )

    assert first_pass == ["nb-NO-Chirp3-HD-Alnilam", "nb-NO-Chirp3-HD-Charon"]
    assert _dialogue_voice_names(repeat_client) == first_pass


def test_synthesize_lesson_audio_given_unprofiled_one_offs_expect_unconstrained_interleaved_pool(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(tmp_path)
    blocks = [
        {
            "id": f"t-{speaker}",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(index, speaker),
        }
        for index, speaker in enumerate(("anna", "bo"))
    ]
    client = _synthesis_client()

    synthesize_lesson_audio(
        lesson_id="unconstrained",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert _dialogue_voice_names(client) == [
        "nb-NO-Chirp3-HD-Leda",
        "nb-NO-Chirp3-HD-Alnilam",
    ]


def test_synthesize_lesson_audio_given_pinned_recurring_cast_expect_stable_compatible_voices(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        character_registry=CharacterRegistry(
            characters={
                "jonas": CharacterDefinition(character_id="jonas", name="Jonas", voice_profile="masculine"),
                "mina": CharacterDefinition(character_id="mina", name="Mina", voice_profile="feminine"),
            }
        ),
        character_voice_overrides={
            "jonas": "nb-NO-Chirp3-HD-Alnilam",
            "mina": "nb-NO-Chirp3-HD-Leda",
        },
    )
    blocks = [
        {
            "id": "t-jonas",
            "text": "Jeg lager kaffe nå.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "jonas", character_id="jonas"),
        },
        {
            "id": "t-mina",
            "text": "Jeg jobber hjemme i dag.",
            "origin": "lesson_reading",
            "source": _dialogue_source(1, "mina", character_id="mina"),
        },
    ]

    first_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="present-tense",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=first_client,
    )
    second_client = _synthesis_client()
    renamed_blocks = [
        {**block, "source": {**block["source"], "speaker_id": f"flat-{block['source']['speaker_id']}"}}
        for block in blocks
    ]
    synthesize_lesson_audio(
        lesson_id="ask-for-help",
        transcript_blocks=renamed_blocks,
        distribution_root=tmp_path / "second",
        settings=settings,
        synthesis_client=second_client,
    )

    assert _dialogue_voice_names(first_client) == [
        "nb-NO-Chirp3-HD-Alnilam",
        "nb-NO-Chirp3-HD-Leda",
    ]
    assert _dialogue_voice_names(second_client) == _dialogue_voice_names(first_client)


def test_synthesize_lesson_audio_given_conflicting_speaker_profiles_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(tmp_path)
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-erik-1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "erik", voice_profile="masculine"),
        },
        {
            "id": "t-erik-2",
            "text": "Hei igjen.",
            "origin": "lesson_reading",
            "source": _dialogue_source(1, "erik", voice_profile="feminine"),
        },
    ]

    with pytest.raises(ValueError, match="conflicting voice_profile"):
        synthesize_lesson_audio(
            lesson_id="conflicting-profile",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_turn_profile_conflicting_registry_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        character_registry=CharacterRegistry(
            characters={"mina": CharacterDefinition(character_id="mina", name="Mina", voice_profile="feminine")}
        ),
        character_voice_overrides={"mina": "nb-NO-Chirp3-HD-Leda"},
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-mina",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "mina", character_id="mina", voice_profile="masculine"),
        }
    ]

    with pytest.raises(ValueError, match="but a turn declares"):
        synthesize_lesson_audio(
            lesson_id="registry-conflict",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_pin_conflicting_registry_profile_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        character_registry=CharacterRegistry(
            characters={"jonas": CharacterDefinition(character_id="jonas", name="Jonas", voice_profile="masculine")}
        ),
        character_voice_overrides={"jonas": "nb-NO-Chirp3-HD-Leda"},
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-jonas",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "jonas", character_id="jonas"),
        }
    ]

    with pytest.raises(ValueError, match="pinned voice"):
        synthesize_lesson_audio(
            lesson_id="pin-conflict",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_registered_profile_without_pin_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        character_registry=CharacterRegistry(
            characters={"kari": CharacterDefinition(character_id="kari", name="Kari", voice_profile="feminine")}
        ),
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-unrelated",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "bo"),
        }
    ]

    with pytest.raises(ValueError, match="no private voice mapping"):
        synthesize_lesson_audio(
            lesson_id="missing-pin",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_profile_without_compatible_pool_voice_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        speaker_voice_pool=("nb-NO-Chirp3-HD-Leda", "nb-NO-Chirp3-HD-Aoede"),
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-erik",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "erik", voice_profile="masculine"),
        }
    ]

    with pytest.raises(ValueError, match="no 'masculine' voice"):
        synthesize_lesson_audio(
            lesson_id="empty-compatible-pool",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_override_conflicting_profile_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        speaker_voice_overrides={"teacher": "nb-NO-Chirp3-HD-Gacrux"},
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-teacher",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "teacher", voice_profile="masculine"),
        }
    ]

    with pytest.raises(ValueError, match="override voice"):
        synthesize_lesson_audio(
            lesson_id="override-conflict",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_unknown_source_profile_expect_no_provider_call(tmp_path: Path, monkeypatch):
    settings = _chirp_settings(tmp_path)
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-nora",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "nora", voice_profile="robotic"),
        }
    ]

    with pytest.raises(ValueError, match="authored vocabulary"):
        synthesize_lesson_audio(
            lesson_id="unknown-profile",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_conflicting_speaker_characters_expect_no_provider_call(
    tmp_path: Path, monkeypatch
):
    settings = _chirp_settings(
        tmp_path,
        character_registry=CharacterRegistry(
            characters={
                "jonas": CharacterDefinition(character_id="jonas", name="Jonas", voice_profile="masculine"),
                "mina": CharacterDefinition(character_id="mina", name="Mina", voice_profile="feminine"),
            }
        ),
        character_voice_overrides={
            "jonas": "nb-NO-Chirp3-HD-Alnilam",
            "mina": "nb-NO-Chirp3-HD-Leda",
        },
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-speaker-1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": _dialogue_source(0, "shared-speaker", character_id="jonas"),
        },
        {
            "id": "t-speaker-2",
            "text": "Hei igjen.",
            "origin": "lesson_reading",
            "source": _dialogue_source(1, "shared-speaker", character_id="mina"),
        },
    ]

    with pytest.raises(ValueError, match="conflicting character_id"):
        synthesize_lesson_audio(
            lesson_id="conflicting-character",
            transcript_blocks=blocks,
            distribution_root=tmp_path,
            settings=settings,
            synthesis_client=client,
        )

    assert client.calls == []


def test_synthesize_lesson_audio_given_changed_mapping_revision_expect_new_cache_identity(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    base_settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        synthesis_revision="cast-v1",
    )
    changed_settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        synthesis_revision="cast-v2",
    )
    blocks = [
        {
            "id": "t-lesson-s1-reading-a1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0},
        }
    ]
    client = _synthesis_client()
    first, _ = synthesize_lesson_audio(
        lesson_id="revision",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=base_settings,
        synthesis_client=client,
    )
    second, _ = synthesize_lesson_audio(
        lesson_id="revision",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=changed_settings,
        synthesis_client=client,
    )

    assert first[0]["path"] != second[0]["path"]
    assert len(client.calls) == 2


def test_synthesize_lesson_audio_given_override_speaker_expect_override_voice_and_reserved_pool(
    tmp_path: Path, monkeypatch
):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B", "nb-NO-Wavenet-C"),
        speaker_voice_overrides={"anna": "nb-NO-Wavenet-B"},
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-anna",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0, "speaker_id": "anna"},
        },
        {
            "id": "t-bo",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 1, "speaker_id": "bo"},
        },
    ]

    synthesize_lesson_audio(
        lesson_id="voice-override",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert _dialogue_voice_names(client) == ["nb-NO-Wavenet-B", "nb-NO-Wavenet-C"]


def test_synthesize_lesson_audio_given_exhausted_pool_expect_deterministic_reuse(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B", "nb-NO-Wavenet-C"),
    )
    blocks = [
        {
            "id": f"t-speaker-{index}",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": index, "speaker_id": speaker},
        }
        for index, speaker in enumerate(("anna", "bo", "cyrus", "dagny"))
    ]
    client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="voice-exhaustion",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )
    first_pass = _dialogue_voice_names(client)

    repeat_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="voice-exhaustion",
        transcript_blocks=blocks,
        distribution_root=tmp_path / "repeat",
        settings=settings,
        synthesis_client=repeat_client,
    )

    assert first_pass == ["nb-NO-Wavenet-B", "nb-NO-Wavenet-C"]
    assert _dialogue_voice_names(repeat_client) == first_pass


def test_synthesize_lesson_audio_given_changed_text_expect_new_cache_identity_and_resynthesis(
    tmp_path: Path, monkeypatch
):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
    )
    client = _synthesis_client()

    def blocks(text: str) -> list[dict[str, object]]:
        return [
            {
                "id": "t-lesson-s1-reading-a1",
                "text": text,
                "origin": "lesson_reading",
                "source": {"section_id": "s1", "block_index": 0},
            }
        ]

    original, _bindings = synthesize_lesson_audio(
        lesson_id="present_tense",
        transcript_blocks=blocks("Hei."),
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )
    repeated, _bindings = synthesize_lesson_audio(
        lesson_id="present_tense",
        transcript_blocks=blocks("Hei."),
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )
    changed, _bindings = synthesize_lesson_audio(
        lesson_id="present_tense",
        transcript_blocks=blocks("Hei igjen."),
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert len(client.calls) == 2
    assert repeated[0]["path"] == original[0]["path"]
    assert changed[0]["path"] != original[0]["path"]
    audio_dir = tmp_path / "dist" / "audio" / "lessons" / "present_tense"
    assert sorted(path.name for path in audio_dir.glob("*.wav")) == [Path(changed[0]["path"]).name]
    assert (tmp_path / "dist" / changed[0]["path"]).is_file()


def test_synthesize_lesson_audio_given_standalone_example_without_pool_expect_default_voice(
    tmp_path: Path, monkeypatch
):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-narration",
            "text": "I dag.",
            "origin": "lesson_example",
            "source": {"section_id": "s1", "block_index": 0},
        }
    ]

    entries, _bindings = synthesize_lesson_audio(
        lesson_id="voice-default",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert _dialogue_voice_names(client) == ["nb-NO-Wavenet-A"]
    assert len(entries) == 1


def test_synthesize_lesson_audio_given_standalone_examples_expect_stable_varied_pool_voices(
    tmp_path: Path, monkeypatch
):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=(
            "nb-NO-Wavenet-B",
            "nb-NO-Wavenet-C",
            "nb-NO-Wavenet-D",
        ),
    )
    blocks = [
        {
            "id": f"example-{index}",
            "text": f"Eksempel {index}.",
            "origin": "lesson_example",
            "source": {"section_id": "s1", "block_index": index},
        }
        for index in range(12)
    ]
    first_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="varied-examples",
        transcript_blocks=blocks,
        distribution_root=tmp_path / "first",
        settings=settings,
        synthesis_client=first_client,
    )
    second_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="varied-examples",
        transcript_blocks=blocks,
        distribution_root=tmp_path / "second",
        settings=settings,
        synthesis_client=second_client,
    )

    first_voices = _dialogue_voice_names(first_client)
    assert len(set(first_voices)) > 1
    assert set(first_voices) <= set(settings.speaker_voice_pool)
    assert _dialogue_voice_names(second_client) == first_voices


def test_synthesize_lesson_audio_given_non_dialogue_reading_expect_default_voice(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
        speaker_voice_pool=("nb-NO-Wavenet-B",),
    )
    client = _synthesis_client()
    blocks = [
        {
            "id": "t-narration",
            "text": "I dag.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0},
        }
    ]

    synthesize_lesson_audio(
        lesson_id="voice-default",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=client,
    )

    assert _dialogue_voice_names(client) == ["nb-NO-Wavenet-A"]


def test_synthesize_lesson_audio_given_cached_asset_expect_no_synthesis_call(tmp_path: Path, monkeypatch):
    account = _account(tmp_path)
    settings = AudioSettings(
        provider="google-cloud-text-to-speech",
        service_account_path=account,
        language="nb-NO",
        voice="nb-NO-Wavenet-A",
    )
    warm_client = _synthesis_client()
    blocks = [
        {
            "id": "t-lesson-s1-reading-a1",
            "text": "Hei.",
            "origin": "lesson_reading",
            "source": {"section_id": "s1", "block_index": 0},
        }
    ]
    synthesize_lesson_audio(
        lesson_id="lesson",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=warm_client,
    )

    cold_client = _synthesis_client()
    synthesize_lesson_audio(
        lesson_id="lesson",
        transcript_blocks=blocks,
        distribution_root=tmp_path,
        settings=settings,
        synthesis_client=cold_client,
    )

    assert cold_client.calls == []


def _wav_bytes(frames: int = 240) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _synthesis_client() -> FakeSynthesisClient:
    return FakeSynthesisClient(_wav_bytes())


def _dialogue_voice_names(client: FakeSynthesisClient) -> list[str]:
    return [voice for _text, voice in client.calls]


def _account(tmp_path: Path) -> Path:
    account = tmp_path / "service-account.json"
    account.write_text(
        json.dumps({"client_email": "test@example.com", "private_key": "unused"}),  # pragma: allowlist secret
        encoding="utf-8",
    )
    return account


def _set_audio_env(
    monkeypatch: pytest.MonkeyPatch,
    account: Path,
    *,
    public_base_url: str | None = None,
) -> None:
    """Set the environment-only audio inputs used by settings loading tests."""
    monkeypatch.setenv("LESSON_AUDIO_SERVICE_ACCOUNT", str(account))
    if public_base_url is None:
        monkeypatch.delenv("LESSON_AUDIO_PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", public_base_url)


def _chirp_settings(tmp_path: Path, **overrides: object) -> AudioSettings:
    """Return settings shaped like the configured Chirp3 HD allocation policy."""
    values: dict[str, object] = {
        "provider": "google-cloud-text-to-speech",
        "service_account_path": _account(tmp_path),
        "language": "nb-NO",
        "voice": "nb-NO-Chirp3-HD-Zephyr",
        "speaker_voice_pool": (
            "nb-NO-Chirp3-HD-Leda",
            "nb-NO-Chirp3-HD-Alnilam",
            "nb-NO-Chirp3-HD-Aoede",
            "nb-NO-Chirp3-HD-Charon",
        ),
        "voice_profiles": {
            "nb-NO-Chirp3-HD-Zephyr": "feminine",
            "nb-NO-Chirp3-HD-Leda": "feminine",
            "nb-NO-Chirp3-HD-Aoede": "feminine",
            "nb-NO-Chirp3-HD-Gacrux": "feminine",
            "nb-NO-Chirp3-HD-Alnilam": "masculine",
            "nb-NO-Chirp3-HD-Charon": "masculine",
        },
    }
    values.update(overrides)
    return AudioSettings(**values)


def _dialogue_source(
    index: int,
    speaker_id: str,
    *,
    character_id: str | None = None,
    voice_profile: str | None = None,
) -> dict[str, object]:
    source: dict[str, object] = {
        "section_id": "s1",
        "block_index": index,
        "speaker_id": speaker_id,
        "dialogue_id": "d1",
    }
    if character_id is not None:
        names = {"jonas": "Jonas", "mina": "Mina", "kari": "Kari"}
        source["character_id"] = character_id
        source["speaker_name"] = names[character_id]
    if voice_profile is not None:
        source["voice_profile"] = voice_profile
    return source
