"""Behavior tests for the shared learner-visibility boundary.

Export projection and audio-candidate derivation must make one decision about
internal request anchors. These tests pin the anchor vocabulary, ordinary
prose preservation, section and callout scoping, and stable source indexes.
"""

from __future__ import annotations

import pytest

from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.services.audio_bindings import audio_binding_key
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.domain.lesson.validation.learner_visibility import block_anchor_text
from lesson_builder.domain.lesson.validation.learner_visibility import is_internal_request_anchor
from lesson_builder.domain.lesson.validation.learner_visibility import is_internal_request_section_title
from lesson_builder.domain.lesson.validation.learner_visibility import visible_block_indexes

K_ANCHOR_VARIANTS = (
    "Request",
    "Request name",
    "Practice request",
    "Activity request",
    "Checkpoint request",
    "Exercise request",
    "Review request",
    "Final retrieval request",
    "Transfer request",
    "Retrieval and review request",
    "Short retrieval and review request",
    "Cumulative request",
    "Cumulative practice request",
    "Retrieval request",
)
K_ANCHOR_PUNCTUATIONS = (":", " - ", " — ")


@pytest.mark.parametrize("anchor", K_ANCHOR_VARIANTS)
@pytest.mark.parametrize("punctuation", K_ANCHOR_PUNCTUATIONS, ids=("colon", "hyphen", "emdash"))
def test_is_internal_request_anchor_given_each_corpus_variant_expect_anchor_recognized(
    anchor: str, punctuation: str
) -> None:
    assert is_internal_request_anchor(f"{anchor}{punctuation}choose-article")
    assert is_internal_request_section_title(f"{anchor}{punctuation}choose-article")


@pytest.mark.parametrize(
    "prose",
    (
        "Compare the central request:",
        "Review the request before you answer:",
        "Context: Use this phrase at work.",
        "Purpose: practise the form.",
        "A request is more effective when it names one clear action:",
        "Checkpoint: build a professional request",
        "Transfer this pattern to your own day:",
    ),
)
def test_is_internal_request_anchor_given_ordinary_prose_expect_not_anchor(prose: str) -> None:
    assert not is_internal_request_anchor(prose)
    assert not is_internal_request_section_title(prose)


def test_visible_block_indexes_given_anchor_tail_expect_original_indexes_kept() -> None:
    blocks = [
        {"kind": "paragraph", "spans": [_txt("Visible.")]},
        {"kind": "paragraph", "spans": [_txt("Request — choose")]},
        _example("et hus"),
    ]

    assert visible_block_indexes(blocks) == [0]


def test_block_anchor_text_given_block_kinds_expect_flattened_text() -> None:
    assert (
        block_anchor_text({"kind": "paragraph", "spans": [{"kind": "strong", "value": "Request: x"}]}) == "Request: x"
    )
    assert block_anchor_text({"kind": "rule", "statement": [_txt("Use ordinal forms.")]}) == "Use ordinal forms."
    assert block_anchor_text(_example()) == "en bok"
    assert block_anchor_text("not-a-block") == ""


@pytest.mark.parametrize("anchor", K_ANCHOR_VARIANTS)
def test_export_given_each_anchor_variant_expect_request_tail_removed(anchor: str) -> None:
    lesson = _lesson(
        [
            _section(
                "s1",
                [
                    {"kind": "paragraph", "spans": [_txt("Before the anchor.")]},
                    _anchor_paragraph(f"{anchor}: choose-article"),
                    {"kind": "paragraph", "spans": [_txt("Internal authoring prose.")]},
                ],
            ),
            _exercise(),
            _section("s2", [{"kind": "paragraph", "spans": [_txt("Next section stays visible.")]}], title="Recap"),
        ]
    )

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    assert exported["sections"][0]["id"] == "s1"
    assert _projected_block_texts(exported)[0] == "Before the anchor."
    assert all("Internal authoring prose." not in text for text in _projected_block_texts(exported))


@pytest.mark.parametrize("anchor", K_ANCHOR_VARIANTS)
def test_export_given_section_title_is_anchor_expect_section_dropped(anchor: str) -> None:
    lesson = _lesson(
        [
            _section("s1", [{"kind": "paragraph", "spans": [_txt("Visible.")]}]),
            _exercise(),
            _section(
                "practice-choose",
                [{"kind": "paragraph", "spans": [_txt("Internal request prose.")]}],
                title=f"{anchor} — choose-article",
            ),
            _section("s2", [{"kind": "paragraph", "spans": [_txt("Recap.")]}], title="Recap"),
        ]
    )

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    assert [section["id"] for section in exported["sections"]] == ["s1", "s2"]


def test_export_given_request_only_section_expect_section_dropped() -> None:
    lesson = _lesson(
        [
            _section(
                "s1",
                [
                    _anchor_paragraph("Request: choose-article"),
                    {"kind": "paragraph", "spans": [_txt("Everything here is internal.")]},
                ],
            ),
            _exercise(),
        ]
    )

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    assert exported["sections"] == []


def test_export_given_anchor_inside_callout_expect_only_callout_tail_removed() -> None:
    callout_with_anchor = {
        "kind": "callout",
        "level": "note",
        "blocks": [
            {"kind": "paragraph", "spans": [_txt("Callout opener.")]},
            _anchor_paragraph("Checkpoint request: choose-article"),
            {"kind": "paragraph", "spans": [_txt("Internal callout tail.")]},
        ],
    }
    lesson = _lesson(
        [
            _section("s1", [callout_with_anchor, {"kind": "paragraph", "spans": [_txt("After the callout.")]}]),
            _exercise(),
        ]
    )

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    section = exported["sections"][0]
    assert [block["kind"] for block in section["blocks"]] == ["callout", "paragraph"]
    assert [block["spans"] for block in section["blocks"][0]["blocks"]] == [[_txt("Callout opener.")]]
    assert section["blocks"][1]["spans"] == [_txt("After the callout.")]


def test_export_given_anchor_between_callouts_expect_following_callout_removed() -> None:
    lesson = _lesson(
        [
            _section(
                "s1",
                [
                    {
                        "kind": "callout",
                        "level": "tip",
                        "blocks": [{"kind": "paragraph", "spans": [_txt("First callout.")]}],
                    },
                    _anchor_paragraph("Activity request — notice-order"),
                    {
                        "kind": "callout",
                        "level": "note",
                        "blocks": [{"kind": "paragraph", "spans": [_txt("Internal callout.")]}],
                    },
                ],
            ),
            _exercise(),
        ]
    )

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    section = exported["sections"][0]
    assert len(section["blocks"]) == 1
    assert section["blocks"][0]["blocks"][0]["spans"] == [_txt("First callout.")]


def test_export_and_audio_given_example_before_and_after_anchor_expect_matching_visibility() -> None:
    elements = [
        _section(
            "s1",
            [
                _example("første eksempel"),
                _reading(),
                _anchor_paragraph("Request: choose-article"),
                _example("andre eksempel"),
            ],
        ),
        _exercise(),
    ]
    lesson = _lesson(elements)

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar")
    section = exported["sections"][0]
    assert [block["kind"] for block in section["blocks"]] == ["example", "reading"]
    assert section["blocks"][0]["no"] == [_txt("første eksempel")]

    candidates = [
        block
        for block in derive_transcript_blocks(_internal(elements))
        if block["origin"] in {"lesson_reading", "lesson_example", "exercise_target"}
    ]
    assert [block["text"] for block in candidates if block["origin"] != "exercise_target"] == [
        "første eksempel",
        "Hei.",
    ]


def test_export_and_audio_given_example_after_anchor_expect_no_orphan_audio_binding() -> None:
    elements = [
        _section(
            "s1",
            [
                _example("første eksempel"),
                _reading(),
                _anchor_paragraph("Request: choose-article"),
                _example("andre eksempel"),
            ],
        ),
        _exercise(),
    ]
    lesson = _lesson(elements)
    candidates = [
        block
        for block in derive_transcript_blocks(_internal(elements))
        if block["origin"] in {"lesson_reading", "lesson_example", "exercise_target"}
    ]
    audio = [
        {
            "id": f"audio-{index}",
            "path": f"audio/lessons/noun_gender/{_audio_uuid(index)}.wav",
            "url": f"https://media.example/audio/lessons/noun_gender/{_audio_uuid(index)}.wav",
            "mime": "audio/wav",
            "status": "synthesized",
        }
        for index in range(len(candidates))
    ]
    bindings = {audio_binding_key(candidate["source"]): f"audio-{index}" for index, candidate in enumerate(candidates)}

    exported = DistributionService().create_lesson_packet(lesson, kind="grammar", audio=audio, audio_bindings=bindings)
    referenced = {block["audio_id"] for block in exported["sections"][0]["blocks"] if block.get("audio_id")}
    assert referenced == {f"audio-{index}" for index in range(len(candidates))}


def test_export_given_anchor_hidden_example_expect_binding_uses_source_index() -> None:
    elements = [
        _section(
            "s1",
            [
                _example("første eksempel"),
                _anchor_paragraph("Request: choose-article"),
                _example("tredje eksempel"),
            ],
        ),
        _exercise(),
    ]
    lesson = _lesson(elements)

    exported = DistributionService().create_lesson_packet(
        lesson,
        kind="grammar",
        audio=[
            {
                "id": "audio-source-0",
                "path": f"audio/lessons/noun_gender/{_audio_uuid(0)}.wav",
                "url": f"https://media.example/audio/lessons/noun_gender/{_audio_uuid(0)}.wav",
                "mime": "audio/wav",
                "status": "synthesized",
            }
        ],
        audio_bindings={"section:s1:block:0": "audio-source-0"},
    )

    section = exported["sections"][0]
    assert section["blocks"][0]["audio_id"] == "audio-source-0"
    assert section["blocks"][0]["id"] == "s1-block-1"


def _audio_uuid(index: int) -> str:
    """Return a deterministic UUID-shaped object key for export fixtures."""
    return f"00000000-0000-5000-8000-{index:012x}"


def _txt(value: str) -> dict[str, str]:
    return {"kind": "text", "value": value}


def _anchor_paragraph(anchor: str) -> dict[str, object]:
    return {"kind": "paragraph", "spans": [_txt(anchor)]}


def _example(no: str = "en bok") -> dict[str, object]:
    return {"kind": "example", "no": [_txt(no)], "en": [_txt("a book")]}


def _reading() -> dict[str, object]:
    return {"kind": "reading", "spans": [_txt("Hei.")], "translation": "Hello."}


def _section(section_id: str, blocks: list[dict[str, object]], *, title: str = "Forms") -> dict[str, object]:
    return {
        "element_kind": "section",
        "id": section_id,
        "role": "model",
        "objective_ids": ["o1"],
        "title": title,
        "blocks": blocks,
    }


def _exercise() -> dict[str, object]:
    return {
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
    }


def _lesson(elements: list[dict[str, object]]) -> Lesson:
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
        "elements": elements,
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
    return Lesson.model_validate(data)


def _internal(elements: list[dict[str, object]]) -> dict[str, object]:
    return {"elements": [dict(element) for element in elements]}


def _projected_block_texts(exported: dict[str, object]) -> list[str]:
    texts: list[str] = []
    sections = exported["sections"]
    for section in sections:
        for block in section["blocks"]:
            texts.append(block_anchor_text(block))
    return texts
