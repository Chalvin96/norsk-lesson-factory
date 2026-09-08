"""Behavior tests for the learner-app export seam."""

from __future__ import annotations

from typing import Any

import pytest

from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.validation.characters import parse_character_registry
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict

K_AUDIO_READING_UUID = "11111111-1111-5111-8111-111111111111"
K_AUDIO_EXAMPLE_UUID = "22222222-2222-5222-8222-222222222222"
K_AUDIO_SINGLE_EXAMPLE_UUID = "33333333-3333-5333-8333-333333333333"


def test_export_given_internal_lesson_expect_pages_and_practice_groups():
    exported = to_export_dict(_lesson(), kind="grammar")

    assert exported["schema_version"] == "4.0"
    assert exported["id"] == "noun_gender"
    assert exported["kind"] == "grammar"
    assert [section["id"] for section in exported["sections"]] == ["s1", "s2"]
    assert [exercise["id"] for exercise in exported["exercises"]] == ["e1"]
    assert exported["practice_groups"] == [{"id": "practice-o1", "objective_id": "o1", "exercise_ids": ["e1"]}]
    assert exported["media"] == {"audio": []}
    ExportedLesson.model_validate(exported)


def test_exported_lesson_given_missing_schema_version_expect_validation_error():
    exported = to_export_dict(_lesson(), kind="grammar")
    exported.pop("schema_version")

    with pytest.raises(ValueError, match="schema_version"):
        ExportedLesson.model_validate(exported)


def test_export_given_recall_audio_target_expect_target_omitted_from_public_payload():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "exercise",
                "id": "e1",
                "operation": "recall_fill",
                "objective_id": "o1",
                "bloom_level": "remember",
                "prompt": [_txt("Fill in the form.")],
                "explanation": None,
                "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
                "payload": {
                    "audio_target": "Huset er stort.",
                    "segments": [
                        {"kind": "span", "spans": [_txt("Huset er ")]},
                        {"kind": "blank", "blank_id": "size", "options": ["stor", "stort"], "answer_index": 1},
                    ],
                },
            }
        ]
    )

    exported = to_export_dict(lesson)

    assert exported["exercises"][0]["payload"] == {
        "segments": [
            {"kind": "span", "spans": [{"kind": "text", "value": "Huset er "}]},
            {"kind": "blank", "blank_id": "size", "options": ["stor", "stort"], "answer_index": 1},
        ]
    }
    ExportedLesson.model_validate(exported)


def test_export_given_reading_and_examples_expect_stable_block_and_turn_ids():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "dialogue",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "A short exchange",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [_txt("Hei.")],
                        "translation": "Hello.",
                        "dialogue_id": "d1",
                        "speaker_id": "anna",
                        "speaker_name": "Anna",
                        "character_id": "anna",
                    },
                    {
                        "kind": "reading",
                        "spans": [_txt("Hei!")],
                        "translation": "Hi!",
                        "dialogue_id": "d1",
                        "speaker_id": "bo",
                        "speaker_name": "Bo",
                    },
                    {
                        "kind": "examples",
                        "items": [{"no": [_txt("en bok")], "en": [_txt("a book")]}],
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
            {
                "element_kind": "section",
                "id": "recap",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Again.")]}],
            },
        ]
    )
    exported = to_export_dict(lesson)
    blocks = exported["sections"][0]["blocks"]

    assert blocks[0]["id"] == "dialogue-block-1"
    assert blocks[0]["turn_index"] == 1
    assert "character_id" not in blocks[0]
    assert blocks[1]["turn_index"] == 2
    assert blocks[2]["items"][0]["id"] == "dialogue-block-3-item-1"
    ExportedLesson.model_validate(exported)


def test_export_given_reading_voice_profile_expect_profile_stripped_from_packet():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "dialogue",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "A short exchange",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [_txt("Skal du til Lillehammer?")],
                        "translation": "Are you going to Lillehammer?",
                        "dialogue_id": "d1",
                        "speaker_id": "erik",
                        "speaker_name": "Erik",
                        "voice_profile": "masculine",
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ]
    )
    exported = to_export_dict(lesson)
    serialized = str(exported)

    assert exported["sections"][0]["blocks"][0]["speaker_id"] == "erik"
    assert exported["sections"][0]["blocks"][0]["speaker_icon_url"].startswith(
        "https://api.dicebear.com/10.x/open-peeps/svg?seed="
    )
    assert "voice_profile" not in exported["sections"][0]["blocks"][0]
    assert "voice_profile" not in serialized
    assert "character_id" not in serialized
    ExportedLesson.model_validate(exported)


def test_speaker_icon_given_same_normalized_name_and_profile_expect_stable_url():
    first = to_export_dict(_dialogue_lesson(speaker_name="  Mina ", voice_profile="feminine", lesson_key="first"))
    second = to_export_dict(_dialogue_lesson(speaker_name="mina", voice_profile="feminine", lesson_key="second"))

    assert _speaker_icon_url(first) == _speaker_icon_url(second)
    assert _speaker_icon_url(first) == _speaker_icon_url(
        to_export_dict(_dialogue_lesson(speaker_name="  Mina ", voice_profile="feminine"))
    )


@pytest.mark.parametrize("speaker_name", ["A", "Kunde"])
def test_speaker_icon_given_generic_label_expect_lesson_local_url(speaker_name: str):
    first = to_export_dict(_dialogue_lesson(speaker_name=speaker_name, lesson_key="first"))
    second = to_export_dict(_dialogue_lesson(speaker_name=speaker_name, lesson_key="second"))

    assert _speaker_icon_url(first) != _speaker_icon_url(second)


def test_speaker_icon_given_registered_profiles_expect_profile_changes_identity_without_exporting_profile():
    lesson = _dialogue_lesson(speaker_name="Mina", character_id="mina")
    authored = _dialogue_lesson(speaker_name="Mina", character_id="mina", voice_profile="feminine")

    feminine = to_export_dict(lesson, character_voice_profiles={"mina": "feminine"})
    masculine = to_export_dict(lesson, character_voice_profiles={"mina": "masculine"})
    authored_feminine = to_export_dict(authored, character_voice_profiles={"mina": "feminine"})

    assert _speaker_icon_url(feminine) != _speaker_icon_url(masculine)
    assert _speaker_icon_url(feminine) == _speaker_icon_url(authored_feminine)
    assert "voice_profile" not in str(feminine)
    assert "feminine" not in _speaker_icon_url(feminine)


def test_speaker_icon_given_unknown_character_metadata_expect_explicit_error():
    lesson = _dialogue_lesson(speaker_name="Mina", character_id="mina")

    with pytest.raises(ValueError, match="unknown recurring character_id 'mina'"):
        to_export_dict(lesson, character_voice_profiles={"erik": "masculine"})


def test_lesson_export_given_character_name_mismatch_expect_explicit_error():
    lesson = _dialogue_lesson(speaker_name="Erik", character_id="mina")
    registry = parse_character_registry(
        {
            "schema_version": 1,
            "characters": {"mina": {"name": "Mina", "voice_profile": "feminine"}},
        }
    )

    with pytest.raises(ValueError, match="registry name is 'Mina'"):
        DistributionService().create_lesson_packet(lesson, character_registry=registry)


def test_lesson_export_given_character_id_without_registry_expect_explicit_error():
    lesson = _dialogue_lesson(speaker_name="Mina", character_id="mina")

    with pytest.raises(ValueError, match="character registry is required"):
        DistributionService().create_lesson_packet(lesson)


def test_export_given_audio_bindings_expect_reading_and_example_audio_ids():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "dialogue",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "A short exchange",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [_txt("Hei.")],
                        "translation": "Hello.",
                    },
                    {
                        "kind": "example",
                        "no": [_txt("Hei igjen.")],
                        "en": [_txt("Hello again.")],
                    },
                    {
                        "kind": "examples",
                        "items": [{"no": [_txt("en bok")], "en": [_txt("a book")]}],
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
            {
                "element_kind": "section",
                "id": "recap",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Again.")]}],
            },
        ]
    )
    exported = to_export_dict(
        lesson,
        audio=[
            {
                "id": "audio-reading",
                "path": f"audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            },
            {
                "id": "audio-example",
                "path": f"audio/lessons/noun_gender/{K_AUDIO_EXAMPLE_UUID}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_EXAMPLE_UUID}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            },
            {
                "id": "audio-single-example",
                "path": f"audio/lessons/noun_gender/{K_AUDIO_SINGLE_EXAMPLE_UUID}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_SINGLE_EXAMPLE_UUID}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            },
        ],
        audio_bindings={
            "section:dialogue:block:0": "audio-reading",
            "section:dialogue:block:1": "audio-single-example",
            "section:dialogue:block:2:item:0": "audio-example",
        },
    )
    blocks = exported["sections"][0]["blocks"]
    assert blocks[0]["audio_id"] == "audio-reading"
    assert blocks[1]["audio_id"] == "audio-single-example"
    assert blocks[2]["items"][0]["audio_id"] == "audio-example"
    ExportedLesson.model_validate(exported)


def test_export_given_exercise_audio_binding_expect_resolvable_exercise_audio_id():
    lesson = _lesson()
    exercise = next(
        element for element in lesson.model_dump(mode="json")["elements"] if element["element_kind"] == "exercise"
    )
    audio_id = f"audio-t-exercise-{exercise['id']}-target"

    exported = to_export_dict(
        lesson,
        audio=[
            {
                "id": audio_id,
                "path": f"audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            }
        ],
        audio_bindings={f"exercise:{exercise['id']}": audio_id},
    )

    assert exported["exercises"][0]["audio_id"] == audio_id
    ExportedLesson.model_validate(exported)


def test_export_given_dangling_audio_binding_expect_validation_error():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "dialogue",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "A short exchange",
                "blocks": [
                    {
                        "kind": "reading",
                        "spans": [_txt("Hei.")],
                        "translation": "Hello.",
                    }
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ]
    )

    with pytest.raises(ValueError, match="audio_id references"):
        to_export_dict(
            lesson,
            audio=[],
            audio_bindings={"section:dialogue:block:0": "audio-missing"},
        )


def test_export_given_unreferenced_synthesized_audio_expect_validation_error():
    with pytest.raises(ValueError, match="synthesized media.audio entries"):
        ExportedLesson.model_validate(
            {
                "schema_version": "4.0",
                "id": "noun_gender",
                "kind": "grammar",
                "language": "nb-NO",
                "title": "Noun gender",
                "cefr_level": "A1",
                "goal": "Tell en, ei, and et apart.",
                "objectives": [{"id": "o1", "statement": "Choose the article."}],
                "content": [{"kind": "section", "id": "s1"}],
                "sections": [
                    {
                        "kind": "section",
                        "id": "s1",
                        "role": "model",
                        "title": "Forms",
                        "objective_ids": ["o1"],
                        "blocks": [],
                    }
                ],
                "exercises": [],
                "practice_groups": [],
                "media": {
                    "audio": [
                        {
                            "id": "orphan",
                            "path": f"audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                            "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_READING_UUID}.wav",
                            "mime": "audio/wav",
                            "status": "synthesized",
                        }
                    ]
                },
            }
        )


def test_export_given_audio_for_another_lesson_expect_validation_error():
    with pytest.raises(ValueError, match="belong to the packet lesson id"):
        ExportedLesson.model_validate(
            {
                "schema_version": "4.0",
                "id": "noun_gender",
                "kind": "grammar",
                "language": "nb-NO",
                "title": "Noun gender",
                "cefr_level": "A1",
                "goal": "Tell en, ei, and et apart.",
                "objectives": [{"id": "o1", "statement": "Choose the article."}],
                "content": [],
                "sections": [],
                "exercises": [],
                "practice_groups": [],
                "media": {
                    "audio": [
                        {
                            "id": "foreign-audio",
                            "path": f"audio/lessons/another_lesson/{K_AUDIO_READING_UUID}.wav",
                            "url": f"https://media.example/audio/lessons/another_lesson/{K_AUDIO_READING_UUID}.wav",
                            "mime": "audio/wav",
                            "status": "synthesized",
                        }
                    ]
                },
            }
        )


def test_export_given_unknown_section_objective_expect_validation_error():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["missing-objective"],
                "title": "Forms",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Forms.")]}],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ]
    )

    with pytest.raises(ValueError, match="objective references"):
        to_export_dict(lesson)


def test_export_given_unknown_review_pool_exercise_expect_validation_error():
    lesson = _lesson(
        review_pool={
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [
                        {
                            "uuid": "11111111-1111-5111-8111-111111111111",
                            "exercise_id": "missing-exercise",
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="review-pool exercise references"):
        to_export_dict(lesson)


def test_export_given_request_notes_in_list_expect_entire_request_tail_removed():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [
                    {"kind": "paragraph", "spans": [_txt("Request — choose-article")]},
                    {
                        "kind": "list",
                        "ordered": False,
                        "items": [
                            [_txt("Purpose: practise the form.")],
                            [_txt("Context: a short exchange.")],
                            [_txt("Authoring prose without a label also stays internal.")],
                        ],
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
            {
                "element_kind": "section",
                "id": "s2",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Again.")]}],
            },
        ]
    )
    exported = to_export_dict(lesson)
    assert [section["id"] for section in exported["sections"]] == ["s2"]


def test_export_given_request_anchor_with_free_prose_expect_free_prose_removed():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [
                    {"kind": "paragraph", "spans": [_txt("Before the activity.")]},
                    {"kind": "paragraph", "spans": [_txt("Request — choose-article")]},
                    {
                        "kind": "paragraph",
                        "spans": [_txt("Use this only to select the internal exercise payload.")],
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ]
    )

    exported = to_export_dict(lesson)
    blocks = exported["sections"][0]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["spans"] == [_txt("Before the activity.")]


def test_export_given_request_label_without_anchor_expect_learner_content_preserved():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "spans": [_txt("Context: Use this phrase at work.")],
                    }
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
            {
                "element_kind": "section",
                "id": "s2",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Again.")]}],
            },
        ]
    )

    exported = to_export_dict(lesson)
    assert exported["sections"][0]["blocks"][0]["spans"] == [_txt("Context: Use this phrase at work.")]


def test_export_given_ordinary_sentence_ending_in_request_expect_following_examples_preserved():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [
                    {"kind": "paragraph", "spans": [_txt("Compare the central request:")]},
                    {
                        "kind": "examples",
                        "items": [{"no": [_txt("Send meg en oversikt.")], "en": [_txt("Send me an overview.")]}],
                    },
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ]
    )

    exported = to_export_dict(
        lesson,
        audio=[
            {
                "id": "audio-example",
                "path": f"audio/lessons/noun_gender/{K_AUDIO_EXAMPLE_UUID}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{K_AUDIO_EXAMPLE_UUID}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            }
        ],
        audio_bindings={"section:s1:block:1:item:0": "audio-example"},
    )

    blocks = exported["sections"][0]["blocks"]
    assert blocks[0]["spans"] == [_txt("Compare the central request:")]
    assert blocks[1]["items"][0]["audio_id"] == "audio-example"


def test_export_given_internal_spans_expect_only_wire_span_kinds():
    lesson = _lesson(
        elements=[
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "spans": [
                            {"kind": "annotated", "value": "bok", "lang": "no", "metadata": {"x": "1"}},
                            {
                                "kind": "sentence",
                                "children": [{"kind": "text", "value": "."}],
                            },
                        ],
                    }
                ],
            },
            _lesson().model_dump(mode="json")["elements"][1],
            {
                "element_kind": "section",
                "id": "s2",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Again.")]}],
            },
        ]
    )
    exported = to_export_dict(lesson)
    spans = exported["sections"][0]["blocks"][0]["spans"]
    assert spans == [
        {"kind": "foreign_term", "value": "bok", "lang": "no"},
        {"kind": "text", "value": "."},
    ]


def _txt(value: str) -> dict[str, str]:
    return {"kind": "text", "value": value}


def _speaker_icon_url(exported: dict[str, Any]) -> str:
    return str(exported["sections"][0]["blocks"][0]["speaker_icon_url"])


def _dialogue_lesson(
    *,
    speaker_name: str,
    voice_profile: str | None = None,
    character_id: str | None = None,
    lesson_key: str = "noun_gender",
) -> Lesson:
    block: dict[str, object] = {
        "kind": "reading",
        "spans": [_txt("Hei.")],
        "translation": "Hello.",
        "dialogue_id": "d1",
        "speaker_id": "speaker-1",
        "speaker_name": speaker_name,
        "voice_profile": voice_profile,
        "character_id": character_id,
    }
    return _lesson(
        key=lesson_key,
        elements=[
            {
                "element_kind": "section",
                "id": "dialogue",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Dialogue",
                "blocks": [block],
            },
            _lesson().model_dump(mode="json")["elements"][1],
        ],
    )


def _lesson(**overrides: object) -> Lesson:
    data: dict[str, object] = {
        "key": "noun_gender",
        "concept_slug": "noun_gender",
        "grounding_mode": "grounded",
        "title": "Noun gender",
        "cefr_level": "A1",
        "goal": "Tell en, ei, and et apart.",
        "objectives": [
            {
                "id": "o1",
                "statement": "Choose the correct article.",
                "bloom_targets": ["remember"],
            }
        ],
        "elements": [
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [{"kind": "paragraph", "spans": [_txt("A short explanation.")]}],
            },
            {
                "element_kind": "exercise",
                "id": "e1",
                "operation": "choose",
                "objective_id": "o1",
                "bloom_level": "remember",
                "prompt": [_txt("Choose the article.")],
                "explanation": None,
                "derived_from": [{"section_id": "s1", "block_index": None, "note": None}],
                "payload": {
                    "options": [
                        {"option_id": "a", "text": "en", "why": None},
                        {"option_id": "b", "text": "et", "why": None},
                    ],
                    "answer_id": "a",
                    "stem": None,
                },
            },
            {
                "element_kind": "section",
                "id": "s2",
                "role": "recap",
                "objective_ids": ["o1"],
                "title": "Recap",
                "blocks": [{"kind": "paragraph", "spans": [_txt("Choose again.")]}],
            },
        ],
        "review_pool": {
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [
                        {
                            "uuid": "11111111-1111-5111-8111-111111111111",
                            "exercise_id": "e1",
                        }
                    ],
                }
            ]
        },
    }
    data.update(overrides)
    return Lesson.model_validate(data)
