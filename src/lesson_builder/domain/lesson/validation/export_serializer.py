"""Entry point: to_export_dict projects an internal lesson to the public wire shape."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping
from typing import Any
from typing import cast
from urllib.parse import urlencode

from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.services.audio_bindings import audio_binding_key
from lesson_builder.domain.lesson.settings import K_EXPORT_SPEAKER_ICON_BASE_URL
from lesson_builder.domain.lesson.settings import K_LESSON_SCHEMA_VERSION
from lesson_builder.domain.lesson.validation.learner_visibility import is_internal_request_section_title
from lesson_builder.domain.lesson.validation.learner_visibility import visible_block_indexes

K_SPAN_WIRE_KINDS = frozenset({"text", "emphasis", "strong", "code", "foreign_term"})
K_SPAN_INTERNAL_KINDS = frozenset({"annotated", "sentence"})
K_ALLOWED_LESSON_KINDS = frozenset({"grammar", "phraseology", "communicative", "pronunciation", "writing"})
K_SPEAKER_ICON_PROFILES = frozenset({"feminine", "masculine"})
K_GENERIC_SPEAKER_LABELS = frozenset(
    {
        "a",
        "b",
        "c",
        "d",
        "ansatt",
        "besøkende",
        "butikkansatt",
        "customer",
        "ekspeditøren",
        "employee",
        "friend",
        "landlord",
        "kunde",
        "læreren",
        "narrator",
        "person",
        "service provider",
        "shop",
        "shop employee",
        "speaker",
        "student",
        "teacher",
        "venn",
        "visitor",
        "waiter",
        "waitress",
    }
)

__all__ = ["to_export_dict"]


def to_export_dict(
    lesson: Lesson,
    *,
    kind: str | None = None,
    language: str = "nb-NO",
    audio: list[dict[str, Any]] | None = None,
    audio_bindings: Mapping[str, str] | None = None,
    character_voice_profiles: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Project an internal lesson to the public packet and validate the result."""
    selected_kind = kind or "grammar"
    if selected_kind not in K_ALLOWED_LESSON_KINDS:
        raise ValueError(f"unknown lesson kind {selected_kind!r}")
    if language != "nb-NO":
        raise ValueError("the public lesson contract currently supports only language 'nb-NO'")
    raw = lesson.model_dump(mode="json")
    sections: list[dict[str, Any]] = []
    exercises: list[dict[str, Any]] = []
    content: list[dict[str, str]] = []
    dialogue_turns: dict[str, int] = {}
    bindings = audio_bindings or {}
    for element in raw["elements"]:
        if element["element_kind"] == "section":
            projected = _build_section(
                element,
                lesson_id=str(raw["key"]),
                dialogue_turns=dialogue_turns,
                audio_bindings=bindings,
                character_voice_profiles=character_voice_profiles,
            )
            if projected is not None:
                sections.append(projected)
                content.append({"kind": "section", "id": projected["id"]})
        else:
            projected = _build_exercise(element, audio_bindings=bindings)
            exercises.append(projected)
            content.append({"kind": "exercise", "id": projected["id"]})

    exercise_ids = {exercise["id"] for exercise in exercises}
    practice_groups = [_build_practice_group(pool, exercise_ids=exercise_ids) for pool in raw["review_pool"]["pools"]]
    projected = {
        "schema_version": K_LESSON_SCHEMA_VERSION,
        "id": raw["key"],
        "kind": selected_kind,
        "language": language,
        "title": raw["title"],
        "cefr_level": raw["cefr_level"],
        "goal": raw["goal"],
        "objectives": [{"id": objective["id"], "statement": objective["statement"]} for objective in raw["objectives"]],
        "content": content,
        "sections": sections,
        "exercises": exercises,
        "practice_groups": practice_groups,
        "media": {"audio": audio or []},
    }
    ExportedLesson.model_validate(projected)
    return projected


def _strip_span(span: object) -> list[dict[str, Any]]:
    """Project one internal inline span into app-visible span dictionaries."""
    if not isinstance(span, dict):
        return [cast("dict[str, Any]", span)]
    kind = span.get("kind")
    if kind in K_SPAN_WIRE_KINDS:
        return [span]
    if kind == "annotated":
        value = span.get("value", "")
        if span.get("lang") is not None:
            return [{"kind": "foreign_term", "value": value, "lang": span["lang"]}]
        return [{"kind": "text", "value": value}]
    if kind == "sentence":
        output: list[dict[str, Any]] = []
        for child in span.get("children") or []:
            output.extend(_strip_span(child))
        return output
    return [span]


def _strip_spans(spans: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for span in spans:
        output.extend(_strip_span(span))
    return output


def _looks_like_spans(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict) and item.get("kind") in K_SPAN_WIRE_KINDS | K_SPAN_INTERNAL_KINDS for item in value
        )
    )


def _strip_spans_in_obj(value: object) -> object:
    if isinstance(value, dict):
        return {key: _strip_spans_in_obj(item) for key, item in value.items()}
    if _looks_like_spans(value):
        return _strip_spans(cast("list[Any]", value))
    if isinstance(value, list):
        return [_strip_spans_in_obj(item) for item in value]
    return value


def _build_block(
    block: dict[str, Any],
    *,
    block_id: str,
    lesson_id: str,
    dialogue_turns: dict[str, int],
    source_section_id: str,
    source_block_path: list[int],
    audio_bindings: Mapping[str, str],
    character_voice_profiles: Mapping[str, str | None] | None,
) -> dict[str, Any] | None:
    """Add stable public IDs and recursively project one learner block."""
    kind = block.get("kind")
    output = {
        key: value for key, value in block.items() if key not in {"id", "turn_index", "character_id", "voice_profile"}
    }
    output["id"] = block_id
    if kind == "reading" and block.get("dialogue_id"):
        _populate_reading_block(
            output,
            block,
            lesson_id=lesson_id,
            dialogue_turns=dialogue_turns,
            character_voice_profiles=character_voice_profiles,
        )
    if kind in {"reading", "example"}:
        _attach_block_audio(output, source_section_id, source_block_path, audio_bindings)
    elif kind == "examples":
        output["items"] = _build_example_items(
            block,
            block_id,
            source_section_id,
            source_block_path,
            audio_bindings,
        )
    elif kind == "callout":
        return _build_callout_block(
            output,
            block,
            block_id=block_id,
            lesson_id=lesson_id,
            dialogue_turns=dialogue_turns,
            source_section_id=source_section_id,
            source_block_path=source_block_path,
            audio_bindings=audio_bindings,
            character_voice_profiles=character_voice_profiles,
        )
    return cast(dict[str, Any], _strip_spans_in_obj(output))


def _populate_reading_block(
    output: dict[str, Any],
    block: dict[str, Any],
    *,
    lesson_id: str,
    dialogue_turns: dict[str, int],
    character_voice_profiles: Mapping[str, str | None] | None,
) -> None:
    """Project dialogue metadata for one reading block."""
    _add_reading_turn_index(output, block, dialogue_turns)
    output["speaker_icon_url"] = _speaker_icon_url(
        speaker_name=str(block["speaker_name"]),
        speaker_id=cast(str | None, block.get("speaker_id")),
        character_id=cast(str | None, block.get("character_id")),
        authored_voice_profile=cast(str | None, block.get("voice_profile")),
        lesson_id=lesson_id,
        character_voice_profiles=character_voice_profiles,
    )


def _build_callout_block(
    output: dict[str, Any],
    block: dict[str, Any],
    *,
    block_id: str,
    lesson_id: str,
    dialogue_turns: dict[str, int],
    source_section_id: str,
    source_block_path: list[int],
    audio_bindings: Mapping[str, str],
    character_voice_profiles: Mapping[str, str | None] | None,
) -> dict[str, Any] | None:
    """Project a callout and omit it when all nested blocks are hidden."""
    children = _build_blocks(
        block.get("blocks", []),
        section_id=block_id,
        lesson_id=lesson_id,
        dialogue_turns=dialogue_turns,
        source_section_id=source_section_id,
        source_path_prefix=source_block_path,
        audio_bindings=audio_bindings,
        character_voice_profiles=character_voice_profiles,
    )
    if not children:
        return None
    output["blocks"] = children
    return cast(dict[str, Any], _strip_spans_in_obj(output))


def _add_reading_turn_index(output: dict[str, Any], block: dict[str, Any], dialogue_turns: dict[str, int]) -> None:
    """Add the one-based turn index for a dialogue reading block."""
    dialogue_id = block.get("dialogue_id")
    if not dialogue_id:
        return
    dialogue_key = str(dialogue_id)
    dialogue_turns[dialogue_key] = dialogue_turns.get(dialogue_key, 0) + 1
    output["turn_index"] = dialogue_turns[dialogue_key]


def _block_source(source_section_id: str, source_block_path: list[int]) -> dict[str, Any]:
    """Build the source locator used to bind generated block audio."""
    source: dict[str, Any] = {"section_id": source_section_id}
    if len(source_block_path) == 1:
        source["block_index"] = source_block_path[0]
    else:
        source["block_path"] = source_block_path
    return source


def _attach_block_audio(
    output: dict[str, Any],
    source_section_id: str,
    source_block_path: list[int],
    audio_bindings: Mapping[str, str],
) -> None:
    """Attach a generated audio ID when the authored block has a binding."""
    source = _block_source(source_section_id, source_block_path)
    audio_id = audio_bindings.get(audio_binding_key(source))
    if audio_id is not None:
        output["audio_id"] = audio_id


def _build_example_items(
    block: dict[str, Any],
    block_id: str,
    source_section_id: str,
    source_block_path: list[int],
    audio_bindings: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Project each item in a multi-example block and attach its audio."""
    items: list[dict[str, Any]] = []
    for index, item in enumerate(block.get("items", [])):
        projected_item = {
            "id": f"{block_id}-item-{index + 1}",
            **{key: value for key, value in item.items() if key != "id"},
        }
        source = {**_block_source(source_section_id, source_block_path), "item_index": index}
        audio_id = audio_bindings.get(audio_binding_key(source))
        if audio_id is not None:
            projected_item["audio_id"] = audio_id
        items.append(projected_item)
    return items


def _build_blocks(
    blocks: list[dict[str, Any]],
    *,
    section_id: str,
    lesson_id: str,
    dialogue_turns: dict[str, int],
    source_section_id: str | None = None,
    source_path_prefix: list[int] | None = None,
    audio_bindings: Mapping[str, str],
    character_voice_profiles: Mapping[str, str | None] | None,
) -> list[dict[str, Any]]:
    """Project learner-visible blocks using the shared visibility boundary.

    Request labels such as ``Purpose:`` and ``Context:`` are ordinary English
    and may legitimately occur in learner prose. They are therefore never
    treated as authoring metadata on their own. The explicit internal request
    anchors (``Request —``, ``Checkpoint request:``, ...) start a terminal
    authoring tail; all following blocks in that containing section are
    internal request material. The decision is shared with audio-candidate
    derivation, and source indexes stay authored so audio bindings remain
    stable.
    """
    projected: list[dict[str, Any]] = []
    source_section_id = source_section_id or section_id
    source_path_prefix = source_path_prefix or []
    for source_block_index in visible_block_indexes(blocks):
        block = blocks[source_block_index]
        block_id = f"{section_id}-block-{len(projected) + 1}"
        output = _build_block(
            block,
            block_id=block_id,
            lesson_id=lesson_id,
            dialogue_turns=dialogue_turns,
            source_section_id=source_section_id,
            source_block_path=source_path_prefix + [source_block_index],
            audio_bindings=audio_bindings,
            character_voice_profiles=character_voice_profiles,
        )
        if output is not None:
            projected.append(output)
    return projected


def _build_section(
    section: dict[str, Any],
    *,
    lesson_id: str,
    dialogue_turns: dict[str, int],
    audio_bindings: Mapping[str, str],
    character_voice_profiles: Mapping[str, str | None] | None,
) -> dict[str, Any] | None:
    title = section.get("title")
    if is_internal_request_section_title(title):
        return None
    section_id = str(section["id"])
    blocks = _build_blocks(
        section.get("blocks", []),
        section_id=section_id,
        lesson_id=lesson_id,
        dialogue_turns=dialogue_turns,
        audio_bindings=audio_bindings,
        character_voice_profiles=character_voice_profiles,
    )
    if not blocks:
        return None
    return {
        "kind": "section",
        "id": section_id,
        "role": section["role"],
        "title": title,
        "objective_ids": list(section.get("objective_ids", [])),
        "blocks": blocks,
    }


def _speaker_icon_url(
    *,
    speaker_name: str,
    speaker_id: str | None,
    character_id: str | None,
    authored_voice_profile: str | None,
    lesson_id: str,
    character_voice_profiles: Mapping[str, str | None] | None,
) -> str:
    """Return one stable Open Peeps URL from provider-neutral metadata."""
    registered_profile: str | None = None
    if character_id is not None and character_voice_profiles is not None:
        if character_id not in character_voice_profiles:
            raise ValueError(f"unknown recurring character_id {character_id!r}")
        registered_profile = character_voice_profiles[character_id]
    if authored_voice_profile and registered_profile and authored_voice_profile != registered_profile:
        raise ValueError(f"character {character_id!r} has conflicting voice_profile metadata")
    voice_profile = authored_voice_profile or registered_profile
    if voice_profile is not None and voice_profile not in K_SPEAKER_ICON_PROFILES:
        raise ValueError(f"speaker voice_profile must be one of {sorted(K_SPEAKER_ICON_PROFILES)!r}")
    raw_identity = character_id if character_id is not None else speaker_name
    normalized_value = _normalize_speaker_identity(raw_identity)
    identity_kind = "character" if character_id is not None else "speaker"
    normalized_identity = f"{identity_kind}:{normalized_value}"
    if character_id is None and _is_generic_speaker_label(speaker_name=speaker_name, speaker_id=speaker_id):
        normalized_lesson_id = _normalize_speaker_identity(lesson_id)
        normalized_identity = f"{normalized_identity}|lesson:{normalized_lesson_id}"
    seed_input = f"{normalized_identity}|profile:{voice_profile or 'unspecified'}"
    seed = hashlib.sha256(seed_input.encode("utf-8")).hexdigest()
    return f"{K_EXPORT_SPEAKER_ICON_BASE_URL}?{urlencode({'seed': seed})}"


def _normalize_speaker_identity(value: str) -> str:
    """Normalize one speaker identity for deterministic avatar seeding."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _is_generic_speaker_label(*, speaker_name: str, speaker_id: str | None) -> bool:
    """Identify labels that should not share avatars across lesson packets."""
    return any(
        _normalize_speaker_identity(value) in K_GENERIC_SPEAKER_LABELS
        for value in (speaker_name, speaker_id)
        if value is not None
    )


def _build_exercise(element: dict[str, Any], *, audio_bindings: Mapping[str, str]) -> dict[str, Any]:
    payload = dict(element["payload"])
    if element["operation"] == "recall_fill":
        payload.pop("audio_target", None)
    projected = cast(
        dict[str, Any],
        _strip_spans_in_obj(
            {
                "kind": "exercise",
                "id": element["id"],
                "operation": element["operation"],
                "objective_id": element["objective_id"],
                "prompt": element["prompt"],
                "explanation": element.get("explanation"),
                "payload": payload,
            }
        ),
    )
    audio_id = audio_bindings.get(f"exercise:{element['id']}")
    if audio_id is not None:
        projected["audio_id"] = audio_id
    return projected


def _build_practice_group(pool: dict[str, Any], *, exercise_ids: set[str]) -> dict[str, Any]:
    """Project one review pool without silently dropping broken card links."""
    pool_objective_id = pool["objective_id"]
    pool_exercise_ids = [card["exercise_id"] for card in pool.get("cards", [])]
    unknown_exercise_ids = set(pool_exercise_ids).difference(exercise_ids)
    if unknown_exercise_ids:
        raise ValueError("review-pool exercise references must resolve: " + ", ".join(sorted(unknown_exercise_ids)))
    return {
        "id": f"practice-{pool_objective_id}",
        "objective_id": pool_objective_id,
        "exercise_ids": pool_exercise_ids,
    }
