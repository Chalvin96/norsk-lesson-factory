"""Entry point: `synthesize_lesson_audio` packages lesson audio through a synthesis client.

Provider-neutral voice allocation and the synthesis cache identity live here;
distribution validation owns provider-independent WAV checks; the Google Cloud TTS
request/token/retry boundary is injected as a small ``synthesize`` client from
``lesson_builder.clients.tts``. This module writes distribution assets and returns
only the minimal public audio inventory; provider credentials and voice settings
never enter the exported packet.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Protocol

import yaml

from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.validate_distribution import validate_audio_asset
from lesson_builder.clients.tts.google import K_GOOGLE_TTS_ENCODING
from lesson_builder.clients.tts.google import K_GOOGLE_TTS_SAMPLE_RATE_HZ
from lesson_builder.clients.tts.google import GoogleTextToSpeechClient
from lesson_builder.domain.lesson.models.blocks import K_READING_VOICE_PROFILES
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.services.audio_bindings import audio_binding_key
from lesson_builder.domain.lesson.validation.characters import validate_character_blocks
from lesson_builder.workspace.paths import WorkspacePaths

K_AUDIO_CONFIG_KEY = "audio"
K_AUDIO_SERVICE_ACCOUNT_ENV = "LESSON_AUDIO_SERVICE_ACCOUNT"
K_AUDIO_PUBLIC_BASE_URL_ENV = "LESSON_AUDIO_PUBLIC_BASE_URL"
K_AUDIO_DEFAULT_PROVIDER = "google-cloud-text-to-speech"
K_AUDIO_DEFAULT_LANGUAGE = "nb-NO"
K_AUDIO_DEFAULT_VOICE = "nb-NO-Chirp3-HD-Zephyr"
K_AUDIO_SPEAKER_VOICE_POOL_KEY = "speaker_voice_pool"
K_AUDIO_SPEAKER_VOICE_OVERRIDES_KEY = "speaker_voice_overrides"
K_AUDIO_CHARACTER_VOICE_OVERRIDES_KEY = "character_voice_overrides"
K_AUDIO_VOICE_PROFILES_KEY = "voice_profiles"
K_AUDIO_SYNTHESIS_REVISION_KEY = "synthesis_revision"
K_AUDIO_DEFAULT_SYNTHESIS_REVISION = "v1"
K_SUPPORTED_LANGUAGE = "nb-NO"
K_AUDIO_FINGERPRINT_LENGTH = 64


class AudioSynthesisClient(Protocol):
    """Minimal synthesis seam: one call per uncached audio candidate."""

    def synthesize(self, *, text: str, voice: str) -> bytes:
        """Synthesize one candidate text in one provider voice."""


@dataclass(frozen=True)
class AudioSettings:
    """Private provider configuration loaded from ``config.yaml``."""

    provider: str
    service_account_path: Path
    language: str
    voice: str
    public_base_url: str | None = None
    speaker_voice_pool: tuple[str, ...] = ()
    speaker_voice_overrides: Mapping[str, str] = field(default_factory=dict)
    character_registry: CharacterRegistry = field(default_factory=lambda: CharacterRegistry(characters={}))
    character_voice_overrides: Mapping[str, str] = field(default_factory=dict)
    voice_profiles: Mapping[str, str] = field(default_factory=dict)
    synthesis_revision: str = K_AUDIO_DEFAULT_SYNTHESIS_REVISION


def validate_lesson_audio_sources(transcript_blocks: list[dict[str, Any]], settings: AudioSettings) -> None:
    """Validate all source identities and voice allocations without provider I/O."""
    allocator = _SpeakerVoiceAllocator(settings)
    allocator.validate_sources(transcript_blocks)
    for block in transcript_blocks:
        origin = block.get("origin")
        if origin not in {"lesson_reading", "lesson_example", "exercise_target"}:
            continue
        candidate_id = block.get("id")
        source = block.get("source")
        text = block.get("text")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("audio candidate id must be non-empty")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"audio candidate {candidate_id!r} text must be non-empty")
        if not isinstance(source, dict):
            raise TypeError(f"audio candidate {candidate_id!r} has no source locator")
        audio_binding_key(source)
        allocator.voice_for(source, candidate_id=candidate_id, origin=origin)


def synthesize_lesson_audio(
    *,
    lesson_id: str,
    transcript_blocks: list[dict[str, Any]],
    distribution_root: Path,
    settings: AudioSettings,
    synthesis_client: AudioSynthesisClient | None = None,
    cache_root: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Synthesize reading/example candidates and return public entries plus bindings.

    When ``cache_root`` is supplied, validated WAVs are retained outside the
    staged distribution by their complete synthesis fingerprint. A failed distribution
    can therefore resume without paying for completed provider work.
    """
    if not lesson_id or "/" in lesson_id or "\\" in lesson_id or lesson_id in {".", ".."}:
        raise ValueError("lesson_id must be a simple distribution directory name")
    audio_dir = WorkspacePaths(Path(distribution_root)).dist_root / "audio" / "lessons" / lesson_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    owned_client: GoogleTextToSpeechClient | None = None
    if synthesis_client is None:
        owned_client = GoogleTextToSpeechClient(
            service_account_path=settings.service_account_path,
            language=settings.language,
        )
        client: AudioSynthesisClient = owned_client
    else:
        client = synthesis_client
    entries: list[dict[str, Any]] = []
    entry_ids: set[str] = set()
    bindings: dict[str, str] = {}
    validate_lesson_audio_sources(transcript_blocks, settings)
    speaker_allocator = _SpeakerVoiceAllocator(settings)
    speaker_allocator.validate_sources(transcript_blocks)
    try:
        for block in transcript_blocks:
            origin = block.get("origin")
            if origin not in {"lesson_reading", "lesson_example", "exercise_target"}:
                continue
            binding, audio_id, entry = _synthesize_audio_candidate(
                block=block,
                lesson_id=lesson_id,
                audio_dir=audio_dir,
                settings=settings,
                speaker_allocator=speaker_allocator,
                client=client,
                cache_root=cache_root,
            )
            if entry is not None and entry["id"] not in entry_ids:
                entries.append(entry)
                entry_ids.add(str(entry["id"]))
            bindings[binding] = audio_id
        if not entries:
            raise ValueError("lesson has no reading/example audio candidates")
        _remove_stale_audio(audio_dir, entries)
    finally:
        if owned_client is not None:
            owned_client.close()
    return entries, bindings


def load_audio_settings(*, repo_root: Path, service_account_path: Path | None = None) -> AudioSettings:
    """Load and validate the configured audio provider for an explicit export."""
    root = Path(repo_root)
    raw_audio = _load_audio_config(root)
    selected_path = _resolve_service_account_path(root, service_account_path)
    validated = _validate_audio_configuration(root, raw_audio)
    return AudioSettings(
        provider=validated.provider,
        service_account_path=selected_path,
        language=validated.language,
        voice=str(validated.raw_audio.get("voice", K_AUDIO_DEFAULT_VOICE)),
        public_base_url=validated.public_base_url,
        speaker_voice_pool=validated.speaker_voice_pool,
        speaker_voice_overrides=validated.speaker_voice_overrides,
        character_registry=validated.character_registry,
        character_voice_overrides=validated.character_voice_overrides,
        voice_profiles=validated.voice_profiles,
        synthesis_revision=validated.synthesis_revision,
    )


def validate_audio_configuration(*, repo_root: Path) -> None:
    """Validate all non-secret audio configuration without provider I/O."""
    root = Path(repo_root)
    _validate_audio_configuration(root, _load_audio_config(root))


@dataclass(frozen=True)
class ValidatedAudioConfiguration:
    """Provider-free, fully validated audio configuration values."""

    raw_audio: dict[str, Any]
    provider: str
    language: str
    public_base_url: str | None
    speaker_voice_pool: tuple[str, ...]
    speaker_voice_overrides: dict[str, str]
    character_registry: CharacterRegistry
    character_voice_overrides: dict[str, str]
    voice_profiles: dict[str, str]
    synthesis_revision: str


def _validate_audio_configuration(root: Path, raw_audio: dict[str, Any]) -> ValidatedAudioConfiguration:
    """Build fully validated provider-free audio configuration values."""
    language = _require_supported_language(raw_audio)
    provider = _require_supported_provider(raw_audio)
    character_registry = load_optional_character_registry(root, search_parents=False) or CharacterRegistry(
        characters={}
    )
    voice_profiles = _load_voice_profiles(raw_audio.get(K_AUDIO_VOICE_PROFILES_KEY))
    character_voice_overrides = _load_character_voice_overrides(
        raw_audio.get(K_AUDIO_CHARACTER_VOICE_OVERRIDES_KEY, {}),
        registry=character_registry,
        voice_profiles=voice_profiles,
    )
    return ValidatedAudioConfiguration(
        raw_audio=raw_audio,
        provider=provider,
        language=language,
        public_base_url=_load_public_base_url(),
        speaker_voice_pool=_load_speaker_voice_pool(raw_audio.get(K_AUDIO_SPEAKER_VOICE_POOL_KEY)),
        speaker_voice_overrides=_load_speaker_voice_overrides(raw_audio.get(K_AUDIO_SPEAKER_VOICE_OVERRIDES_KEY, {})),
        character_registry=character_registry,
        character_voice_overrides=character_voice_overrides,
        voice_profiles=voice_profiles,
        synthesis_revision=_require_synthesis_revision(raw_audio).strip(),
    )


def _load_audio_config(root: Path) -> dict[str, Any]:
    """Read and validate the audio mapping from the repository config."""
    config_path = root / "config.yaml"
    raw_config: object = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    raw_audio = raw_config.get(K_AUDIO_CONFIG_KEY, {}) if isinstance(raw_config, dict) else {}
    if not isinstance(raw_audio, dict):
        raise TypeError("config.yaml audio must be a mapping")
    return raw_audio


def _resolve_service_account_path(root: Path, configured_path: Path | None) -> Path:
    """Resolve and require the service-account file used by Google TTS."""
    selected_path = configured_path
    if selected_path is None:
        env_path = os.environ.get(K_AUDIO_SERVICE_ACCOUNT_ENV)
        selected_path = Path(os.path.expanduser(env_path)) if env_path else None
    if selected_path is None:
        raise ValueError(f"audio service account path is required; set {K_AUDIO_SERVICE_ACCOUNT_ENV}")
    selected_path = selected_path if selected_path.is_absolute() else root / selected_path
    if not selected_path.is_file():
        raise FileNotFoundError(f"audio service account not found at {selected_path}")
    return selected_path


def _require_supported_language(raw_audio: dict[str, Any]) -> str:
    """Return the configured language when it is supported by the exporter."""
    language = str(raw_audio.get("language", K_AUDIO_DEFAULT_LANGUAGE))
    if language != K_SUPPORTED_LANGUAGE:
        raise ValueError(f"audio currently supports only {K_SUPPORTED_LANGUAGE!r}")
    return language


def _require_supported_provider(raw_audio: dict[str, Any]) -> str:
    """Return the configured provider when it is supported by the exporter."""
    provider = str(raw_audio.get("provider", K_AUDIO_DEFAULT_PROVIDER))
    if provider != K_AUDIO_DEFAULT_PROVIDER:
        raise ValueError(f"unsupported audio provider {provider!r}")
    return provider


def _require_synthesis_revision(raw_audio: dict[str, Any]) -> str:
    """Return the non-empty revision used in cache identity."""
    revision = raw_audio.get(K_AUDIO_SYNTHESIS_REVISION_KEY, K_AUDIO_DEFAULT_SYNTHESIS_REVISION)
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("audio.synthesis_revision must be a non-empty string")
    return revision.strip()


def _load_public_base_url() -> str | None:
    """Return the optional HTTPS base URL used for public audio entries."""
    public_base_url = os.environ.get(K_AUDIO_PUBLIC_BASE_URL_ENV)
    if public_base_url is not None and (
        not isinstance(public_base_url, str) or not public_base_url.startswith("https://")
    ):
        raise ValueError(f"{K_AUDIO_PUBLIC_BASE_URL_ENV} must be an absolute HTTPS URL")
    return public_base_url.rstrip("/") if public_base_url else None


def _synthesize_audio_candidate(
    *,
    block: dict[str, Any],
    lesson_id: str,
    audio_dir: Path,
    settings: AudioSettings,
    speaker_allocator: _SpeakerVoiceAllocator,
    client: AudioSynthesisClient,
    cache_root: Path | None,
) -> tuple[str, str, dict[str, Any] | None]:
    """Synthesize one candidate and return its binding, ID, and new entry."""
    text = block.get("text")
    candidate_id = block.get("id")
    source = block.get("source")
    origin = block.get("origin")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("audio candidate text must be non-empty")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("audio candidate id must be non-empty")
    if not isinstance(source, dict):
        raise TypeError(f"audio candidate {candidate_id!r} has no source locator")
    binding = audio_binding_key(source)
    voice = speaker_allocator.voice_for(source, candidate_id=candidate_id, origin=origin)
    fingerprint = _synthesis_fingerprint(settings=settings, voice=voice, text=text)
    opaque_id = str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint))
    audio_id = f"audio-{opaque_id}"
    filename = f"{opaque_id}.wav"
    destination = audio_dir / filename
    _ensure_synthesized_audio(
        destination=destination,
        fingerprint=fingerprint,
        text=text,
        voice=voice,
        client=client,
        cache_root=cache_root,
    )
    duration_ms = validate_audio_asset(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    entry = {
        "id": audio_id,
        "path": f"audio/lessons/{lesson_id}/{filename}",
        "mime": "audio/wav",
        "status": "synthesized",
        "duration_ms": duration_ms,
        # The consuming app compares this field with the raw
        # hexadecimal digest returned by hashlib.
        "sha256": digest,
    }
    return binding, audio_id, entry


def _ensure_synthesized_audio(
    *,
    destination: Path,
    fingerprint: str,
    text: str,
    voice: str,
    client: AudioSynthesisClient,
    cache_root: Path | None,
) -> None:
    """Reuse a valid destination/cache file or synthesize the candidate."""
    _discard_invalid_audio(destination)
    cache_file = Path(cache_root) / f"{fingerprint}.wav" if cache_root is not None else None
    _discard_invalid_audio(cache_file)
    if not destination.exists() and cache_file is not None and cache_file.exists():
        shutil.copy2(cache_file, destination)
    if destination.exists():
        return
    audio_bytes = client.synthesize(text=text, voice=voice)
    provider_destination = cache_file or destination
    provider_destination.parent.mkdir(parents=True, exist_ok=True)
    _write_validated_wav(provider_destination, audio_bytes)
    if cache_file is not None:
        shutil.copy2(cache_file, destination)


def _discard_invalid_audio(path: Path | None) -> None:
    """Remove an existing audio file when it is not a valid WAV asset."""
    if path is None or not path.exists():
        return
    try:
        validate_audio_asset(path)
    except ValueError:
        path.unlink()


def _remove_stale_audio(audio_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Remove WAV files no longer declared by the current transcript."""
    declared_files = {Path(entry["path"]).name for entry in entries}
    for stale_file in audio_dir.glob("*.wav"):
        if stale_file.name not in declared_files:
            stale_file.unlink()


def _load_speaker_voice_pool(raw_pool: object) -> tuple[str, ...]:
    """Load the ordered private voice pool without inferring speaker profiles."""
    if raw_pool is None:
        return ()
    if not isinstance(raw_pool, list):
        raise TypeError("audio.speaker_voice_pool must be a list of provider voices")
    pool: list[str] = []
    for voice in raw_pool:
        if not isinstance(voice, str) or not voice.strip():
            raise ValueError("audio.speaker_voice_pool entries must be non-empty strings")
        normalized_voice = voice.strip()
        if normalized_voice in pool:
            raise ValueError(f"audio.speaker_voice_pool repeats provider voice {normalized_voice!r}")
        pool.append(normalized_voice)
    return tuple(pool)


def _load_speaker_voice_overrides(raw_overrides: object) -> dict[str, str]:
    """Load the small explicit speaker-to-voice exception map."""
    if not isinstance(raw_overrides, dict):
        raise TypeError("audio.speaker_voice_overrides must be a mapping")
    overrides: dict[str, str] = {}
    for speaker_id, voice in raw_overrides.items():
        if not isinstance(speaker_id, str) or not speaker_id.strip() or not isinstance(voice, str) or not voice.strip():
            raise ValueError("audio.speaker_voice_overrides keys and values must be non-empty strings")
        overrides[speaker_id.strip()] = voice.strip()
    return overrides


def _load_voice_profiles(raw_profiles: object) -> dict[str, str]:
    """Load the private voice classification as a voice-to-profile mapping."""
    if raw_profiles is None:
        return {}
    if not isinstance(raw_profiles, dict):
        raise TypeError("audio.voice_profiles must be a mapping of profile to voices")
    classification: dict[str, str] = {}
    for profile, voices in raw_profiles.items():
        _validate_voice_profile(profile, voices)
        for voice in voices:
            _add_voice_profile(classification, profile, voice)
    return classification


def _validate_voice_profile(profile: object, voices: object) -> None:
    """Validate one authored voice-profile group."""
    if profile not in K_READING_VOICE_PROFILES:
        raise ValueError(
            f"audio.voice_profiles profile {profile!r} is not in the authored "
            f"vocabulary {sorted(K_READING_VOICE_PROFILES)!r}"
        )
    if not isinstance(voices, list):
        raise TypeError(f"audio.voice_profiles {profile!r} must be a list of voices")


def _add_voice_profile(classification: dict[str, str], profile: object, voice: object) -> None:
    """Add one validated voice to its profile classification."""
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError(f"audio.voice_profiles {profile!r} entries must be non-empty strings")
    normalized_voice = voice.strip()
    previous_profile = classification.get(normalized_voice)
    if previous_profile is not None and previous_profile != profile:
        raise ValueError(f"voice {normalized_voice!r} is classified as both {previous_profile!r} and {profile!r}")
    classification[normalized_voice] = str(profile)


def _load_character_voice_overrides(
    raw_overrides: object,
    *,
    registry: CharacterRegistry,
    voice_profiles: Mapping[str, str],
) -> dict[str, str]:
    """Load private character voice mappings and reject unknown identities."""
    if not isinstance(raw_overrides, dict):
        raise TypeError("audio.character_voice_overrides must be a mapping")
    overrides: dict[str, str] = {}
    for character_id, voice in raw_overrides.items():
        if (
            not isinstance(character_id, str)
            or not character_id.strip()
            or not isinstance(voice, str)
            or not voice.strip()
        ):
            raise ValueError("audio.character_voice_overrides keys and values must be non-empty strings")
        normalized_id = character_id.strip()
        if normalized_id not in registry.characters:
            raise ValueError(f"audio.character_voice_overrides references unknown character_id {normalized_id!r}")
        normalized_voice = voice.strip()
        registry_profile = registry.characters[normalized_id].voice_profile
        voice_profile = voice_profiles.get(normalized_voice)
        if registry_profile is not None and voice_profile is None:
            raise ValueError(
                f"character {normalized_id!r} is {registry_profile!r} but pinned voice "
                f"{normalized_voice!r} is not classified in audio.voice_profiles"
            )
        if registry_profile is not None and voice_profile is not None and voice_profile != registry_profile:
            raise ValueError(
                f"character {normalized_id!r} is {registry_profile!r} but pinned voice "
                f"{normalized_voice!r} is classified {voice_profile!r}"
            )
        overrides[normalized_id] = normalized_voice
    return overrides


def _write_validated_wav(destination: Path, audio_bytes: bytes) -> None:
    """Validate provider bytes before atomically publishing the final asset."""
    if not audio_bytes:
        raise ValueError("Google TTS returned an empty audio asset")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(audio_bytes)
        validate_audio_asset(temporary)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class _SpeakerVoiceAllocator:
    """Assign dialogue speakers compatible voices deterministically.

    Allocation is per lesson and deterministic: the same transcript blocks
    always produce the same speaker-to-voice mapping, so synthesized cache
    filenames stay stable. Standalone examples select independently from the
    configured pool using their stable candidate id; non-dialogue readings use
    the narrator default. Dialogue voices resolve through the private recurring
    character pin, a compatible local speaker override, the profile-compatible
    pool subset, and finally the unconstrained pool.

    An authored ``voice_profile`` filters pool allocation to voices classified
    with the same profile. Profile-compatible pool exhaustion reuses compatible
    voices from the start of that subset. Unprofiled one-off speakers keep the
    unconstrained first-appearance allocation over the whole pool. Overrides and
    pins reserve their voice, so an allocated speaker never shares it. No
    profile is inferred from names, pronouns, or text; only authored metadata
    is consulted.
    """

    def __init__(self, settings: AudioSettings) -> None:
        self._settings = settings
        self._assigned: dict[str, str] = {}
        self._used: set[str] = set(settings.speaker_voice_overrides.values())
        self._used.update(settings.character_voice_overrides.values())
        self._reuse_counts: dict[str | None, int] = {}

    def validate_sources(self, transcript_blocks: list[dict[str, Any]]) -> None:
        """Validate identities, profiles, and pins before any provider call."""
        sources = [
            block.get("source")
            for block in transcript_blocks
            if block.get("origin") in {"lesson_reading", "lesson_example"}
        ]
        valid_sources = [source for source in sources if isinstance(source, Mapping)]
        validate_character_blocks(self._settings.character_registry, valid_sources)
        speaker_characters: dict[str, str] = {}
        speaker_profiles: dict[str, str | None] = {}
        for source in valid_sources:
            self._validate_source(
                source,
                speaker_characters=speaker_characters,
                speaker_profiles=speaker_profiles,
            )
        for character_id in self._settings.character_registry.characters:
            self._validate_character_profile(character_id, source_profile=None)

    def voice_for(
        self,
        source: Mapping[str, Any],
        *,
        candidate_id: str | None = None,
        origin: str | None = None,
    ) -> str:
        """Return the provider voice for one transcript block source locator."""
        character_id = source.get("character_id")
        if isinstance(character_id, str) and character_id.strip():
            return self._character_voice(character_id.strip())
        speaker_id = source.get("speaker_id")
        if not isinstance(speaker_id, str) or not speaker_id.strip():
            if origin == "lesson_example" and candidate_id:
                return self._example_voice(candidate_id)
            return self._settings.voice
        speaker_id = speaker_id.strip()
        override = self._settings.speaker_voice_overrides.get(speaker_id)
        if override is not None:
            return override
        if speaker_id in self._assigned:
            return self._assigned[speaker_id]
        profile = _source_voice_profile(source)
        voice = self._next_pool_voice(profile)
        self._assigned[speaker_id] = voice
        return voice

    def _validate_source(
        self,
        source: Mapping[str, Any],
        *,
        speaker_characters: dict[str, str],
        speaker_profiles: dict[str, str | None],
    ) -> None:
        """Validate one source's speaker, character, and voice-profile metadata."""
        speaker_id = _normalize_optional(source.get("speaker_id"))
        character_id = _normalize_optional(source.get("character_id"))
        profile = _source_voice_profile(source)
        if profile is not None and speaker_id is None:
            raise ValueError("voice_profile requires a dialogue speaker_id")
        if speaker_id is not None:
            self._validate_speaker_metadata(
                speaker_id,
                character_id,
                profile,
                speaker_characters=speaker_characters,
                speaker_profiles=speaker_profiles,
            )
        if character_id is not None:
            # A pinned recurring character resolves through its pin, so the
            # override and pool stages never apply to this turn.
            self._validate_character_profile(character_id, source_profile=profile)
            return
        if profile is None or speaker_id is None:
            return
        override = self._settings.speaker_voice_overrides.get(speaker_id)
        if override is None:
            self._require_compatible_pool_voice(profile)
            return
        override_profile = self._settings.voice_profiles.get(override)
        if override_profile is not None and override_profile != profile:
            raise ValueError(
                f"speaker {speaker_id!r} declares {profile!r} but its override "
                f"voice {override!r} is classified {override_profile!r}"
            )

    def _validate_speaker_metadata(
        self,
        speaker_id: str,
        character_id: str | None,
        profile: str | None,
        *,
        speaker_characters: dict[str, str],
        speaker_profiles: dict[str, str | None],
    ) -> None:
        """Reject conflicting metadata for a repeated dialogue speaker."""
        previous_character = speaker_characters.setdefault(speaker_id, character_id or "")
        if previous_character != (character_id or ""):
            raise ValueError(f"speaker {speaker_id!r} declares conflicting character_id metadata within one lesson")
        previous_profile = speaker_profiles.setdefault(speaker_id, profile)
        if previous_profile != profile:
            raise ValueError(f"speaker {speaker_id!r} declares conflicting voice_profile metadata within one lesson")

    def _example_voice(self, candidate_id: str) -> str:
        """Select one stable pool voice for a standalone lesson example."""
        pool = self._settings.speaker_voice_pool
        if not pool:
            return self._settings.voice
        digest = hashlib.sha256(candidate_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(pool)
        return pool[index]

    def _validate_character_profile(self, character_id: str, *, source_profile: str | None) -> None:
        """Reject profile conflicts between a turn, the registry, and the pin."""
        definition = self._settings.character_registry.characters.get(character_id)
        if definition is None:
            raise ValueError(f"unknown recurring character_id {character_id!r}")
        registry_profile = definition.voice_profile
        if registry_profile is not None and source_profile is not None and registry_profile != source_profile:
            raise ValueError(
                f"character {character_id!r} is registered {registry_profile!r} but a turn declares {source_profile!r}"
            )
        pin = self._settings.character_voice_overrides.get(character_id)
        if pin is None:
            if source_profile is not None or registry_profile is not None:
                raise ValueError(f"no private voice mapping configured for character_id {character_id!r}")
            return
        effective_profile = source_profile or registry_profile
        pin_profile = self._settings.voice_profiles.get(pin)
        if effective_profile is not None and pin_profile is not None and pin_profile != effective_profile:
            raise ValueError(
                f"character {character_id!r} resolves to {effective_profile!r} but "
                f"pinned voice {pin!r} is classified {pin_profile!r}"
            )

    def _require_compatible_pool_voice(self, profile: str) -> None:
        """Fail closed when no pool voice carries the requested profile."""
        if not any(self._settings.voice_profiles.get(voice) == profile for voice in self._settings.speaker_voice_pool):
            raise ValueError(f"no {profile!r} voice is configured in audio.speaker_voice_pool")

    def _character_voice(self, character_id: str) -> str:
        """Return the explicitly configured voice for one recurring character."""
        if character_id not in self._settings.character_registry.characters:
            raise ValueError(f"unknown recurring character_id {character_id!r}")
        voice = self._settings.character_voice_overrides.get(character_id)
        if voice is None:
            raise ValueError(f"no private voice mapping configured for character_id {character_id!r}")
        return voice

    def _next_pool_voice(self, profile: str | None) -> str:
        """Allocate the next unused compatible pool voice deterministically."""
        pool = self._settings.speaker_voice_pool
        candidates = [voice for voice in pool if profile is None or self._settings.voice_profiles.get(voice) == profile]
        if profile is not None and not candidates:
            raise ValueError(f"no {profile!r} voice is configured in audio.speaker_voice_pool")
        available = [voice for voice in candidates if voice not in self._used]
        if available:
            voice = available[0]
            self._used.add(voice)
            return voice
        if not pool:
            return self._settings.voice
        reuse_index = self._reuse_counts.get(profile, 0)
        self._reuse_counts[profile] = reuse_index + 1
        return candidates[reuse_index % len(candidates)]


def _normalize_optional(value: object) -> str | None:
    """Return one stripped non-empty string, or ``None`` when absent/blank."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _source_voice_profile(source: Mapping[str, Any]) -> str | None:
    """Return one validated authored profile, or ``None`` when unconstrained.

    The value is never inferred: only an explicit non-empty string from the
    authored vocabulary passes, and any other shape fails closed.
    """
    raw_profile = source.get("voice_profile")
    if raw_profile is None:
        return None
    if not isinstance(raw_profile, str) or not raw_profile.strip():
        raise ValueError("voice_profile must be a non-empty string when present")
    profile = raw_profile.strip()
    if profile not in K_READING_VOICE_PROFILES:
        raise ValueError(
            f"unknown voice_profile {profile!r}; the authored vocabulary is {sorted(K_READING_VOICE_PROFILES)!r}"
        )
    return profile


def _synthesis_fingerprint(*, settings: AudioSettings, voice: str, text: str) -> str:
    """Identify one synthesis input without exporting private provider settings.

    The exact Norwegian text belongs to the cache identity next to the private
    synthesis settings and the selected voice, so an edited source line can
    never reuse the previous recording.
    """
    identity = json.dumps(
        [
            settings.provider,
            settings.language,
            voice,
            text,
            K_GOOGLE_TTS_ENCODING,
            K_GOOGLE_TTS_SAMPLE_RATE_HZ,
            settings.synthesis_revision,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:K_AUDIO_FINGERPRINT_LENGTH]


__all__ = [
    "AudioSettings",
    "load_audio_settings",
    "synthesize_lesson_audio",
    "validate_audio_configuration",
]
