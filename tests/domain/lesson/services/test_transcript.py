"""Behavior tests for pure transcript-block derivation."""

from __future__ import annotations

import pytest

from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks


def test_derive_transcript_blocks_given_character_metadata_expect_private_source_identity():
    blocks = derive_transcript_blocks(
        {
            "elements": [
                {
                    "element_kind": "section",
                    "id": "dialogue",
                    "blocks": [
                        {
                            "kind": "reading",
                            "spans": [{"kind": "text", "value": "Hei."}],
                            "translation": "Hello.",
                            "dialogue_id": "d1",
                            "speaker_id": "anna-local",
                            "speaker_name": "Anna",
                            "character_id": "anna",
                        }
                    ],
                }
            ]
        }
    )

    assert blocks[0]["source"]["character_id"] == "anna"


def test_derive_transcript_blocks_given_voice_profile_expect_internal_propagation():
    blocks = derive_transcript_blocks(
        {
            "elements": [
                {
                    "element_kind": "section",
                    "id": "dialogue",
                    "blocks": [
                        {
                            "kind": "reading",
                            "spans": [{"kind": "text", "value": "Skal du til Lillehammer?"}],
                            "translation": "Are you going to Lillehammer?",
                            "dialogue_id": "station_help",
                            "speaker_id": "erik",
                            "speaker_name": "Erik",
                            "voice_profile": "masculine",
                        },
                        {
                            "kind": "reading",
                            "spans": [{"kind": "text", "value": "Ja."}],
                            "translation": "Yes.",
                            "dialogue_id": "station_help",
                            "speaker_id": "mina",
                            "speaker_name": "Mina",
                            "character_id": "mina",
                        },
                    ],
                }
            ]
        }
    )

    assert blocks[0]["source"]["voice_profile"] == "masculine"
    assert "voice_profile" not in blocks[1]["source"]
    assert blocks[1]["source"]["character_id"] == "mina"


def test_derive_transcript_blocks_given_foreign_reading_span_expect_no_audio_candidate():
    with pytest.raises(ValueError, match="at least one audio candidate"):
        derive_transcript_blocks(
            {
                "elements": [
                    {
                        "element_kind": "section",
                        "id": "dialogue",
                        "blocks": [
                            {
                                "kind": "reading",
                                "spans": [{"kind": "foreign_term", "value": "hello", "lang": "en"}],
                            }
                        ],
                    }
                ]
            }
        )


def test_derive_transcript_blocks_given_english_speak_target_expect_no_audio_candidate():
    with pytest.raises(ValueError, match="at least one audio candidate"):
        derive_transcript_blocks(
            {
                "elements": [
                    {
                        "element_kind": "exercise",
                        "id": "speak-english",
                        "operation": "speak",
                        "payload": {"target": "hello"},
                    }
                ]
            }
        )


def test_derive_transcript_blocks_given_request_section_expect_no_candidates() -> None:
    internal = _internal(
        [
            _section(
                "practice-choose",
                [_reading()],
                title="Practice request — choose-article",
            )
        ]
    )

    with pytest.raises(ValueError, match="at least one audio candidate"):
        derive_transcript_blocks(internal)


def test_derive_transcript_blocks_given_recall_audio_target_expect_target_without_context_labels():
    blocks = derive_transcript_blocks(
        {
            "elements": [
                {
                    "element_kind": "exercise",
                    "id": "number-sequence-recall",
                    "operation": "recall_fill",
                    "payload": {
                        "audio_target": "åtte bøker, tre epler, ti bord, seks egg og en kopp.",
                        "segments": [],
                    },
                }
            ]
        }
    )

    assert blocks[0]["text"] == "åtte bøker, tre epler, ti bord, seks egg og en kopp."


def test_derive_transcript_blocks_given_norwegian_homographs_expect_candidates_kept():
    blocks = derive_transcript_blocks(
        {
            "elements": [
                _section(
                    "nature-reading",
                    [
                        {
                            "kind": "reading",
                            "spans": [_txt("En and svømmer i dammen.")],
                            "translation": "A duck swims in the pond.",
                        }
                    ],
                ),
                {
                    "element_kind": "exercise",
                    "id": "recall-homograph",
                    "operation": "recall_fill",
                    "payload": {
                        "audio_target": "Vi planlegger et arrangement i morgen.",
                        "segments": [],
                    },
                },
            ]
        }
    )

    assert [block["text"] for block in blocks] == [
        "En and svømmer i dammen.",
        "Vi planlegger et arrangement i morgen.",
    ]


def test_derive_transcript_blocks_given_recall_audio_target_with_english_expect_rejected():
    with pytest.raises(ValueError, match="Norwegian only"):
        derive_transcript_blocks(
            {
                "elements": [
                    {
                        "element_kind": "exercise",
                        "id": "mixed-context",
                        "operation": "recall_fill",
                        "payload": {
                            "audio_target": "8 books: åtte bøker.",
                            "segments": [],
                        },
                    }
                ]
            }
        )


def _txt(value: str) -> dict[str, str]:
    return {"kind": "text", "value": value}


def _reading(spans: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"kind": "reading", "spans": spans or [_txt("Hei.")], "translation": "Hello."}


def _section(section_id: str, blocks: list[dict[str, object]], *, title: str = "Forms") -> dict[str, object]:
    return {
        "element_kind": "section",
        "id": section_id,
        "role": "model",
        "objective_ids": ["o1"],
        "title": title,
        "blocks": blocks,
    }


def _internal(elements: list[dict[str, object]]) -> dict[str, object]:
    return {"elements": [dict(element) for element in elements]}
