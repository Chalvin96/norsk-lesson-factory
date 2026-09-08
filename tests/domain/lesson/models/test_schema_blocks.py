import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.blocks import Block

_adapter = TypeAdapter(Block)


def _txt(v: str) -> dict:
    return {"kind": "text", "value": v}


def test_heading_given_valid_payload_expect_parsed():
    blk = _adapter.validate_python({"kind": "heading", "level": 3, "spans": [_txt("Forms")]})
    assert blk.level == 3


def test_heading_given_invalid_level_expect_validation_error():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "heading", "level": 2, "spans": [_txt("x")]})


def test_table_given_language_header_mismatch_expect_validation_error():
    bad = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Indef")], [_txt("Def")]],  # 3 cols
        "rows": [],
    }
    with pytest.raises(ValidationError, match="col_langs"):
        _adapter.validate_python(bad)


def test_table_given_row_width_mismatch_expect_validation_error():
    bad = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Form")]],
        "rows": [[[_txt("en")]]],  # 1 cell, expected 2
    }
    with pytest.raises(ValidationError, match="col_langs"):
        _adapter.validate_python(bad)


def test_table_given_well_formed_paradigm_expect_parsed():
    ok = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Indefinite")]],
        "rows": [[[_txt("neuter")], [{"kind": "foreign_term", "value": "stort", "lang": "no"}]]],
    }
    blk = _adapter.validate_python(ok)
    assert blk.col_langs == ["en", "no"]


def test_callout_given_nested_blocks_expect_parsed():
    blk = _adapter.validate_python(
        {"kind": "callout", "level": "tip", "blocks": [{"kind": "paragraph", "spans": [_txt("hi")]}]}
    )
    assert blk.blocks[0].kind == "paragraph"


def test_reading_block_given_translation_metadata_expect_typed_reading_block():
    blk = _adapter.validate_python({"kind": "reading", "spans": [_txt("Hei.")], "translation": "Hello."})
    assert blk.kind == "reading"
    assert blk.translation == "Hello."


def test_reading_block_given_dialogue_metadata_expect_named_turn():
    blk = _adapter.validate_python(
        {
            "kind": "reading",
            "spans": [_txt("Unnskyld, hvor er stasjonen?")],
            "translation": "Excuse me, where is the station?",
            "dialogue_id": "directions-1",
            "speaker_id": "anna",
            "speaker_name": "Anna",
        }
    )

    assert blk.dialogue_id == "directions-1"
    assert blk.speaker_name == "Anna"


def test_reading_block_given_recurring_character_expect_local_speaker_preserved():
    blk = _adapter.validate_python(
        {
            "kind": "reading",
            "spans": [_txt("Hei!")],
            "translation": "Hello!",
            "dialogue_id": "directions-1",
            "speaker_id": "anna-local",
            "speaker_name": "Anna",
            "character_id": "anna",
        }
    )

    assert blk.speaker_id == "anna-local"
    assert blk.character_id == "anna"


def test_reading_block_given_partial_dialogue_metadata_expect_validation_error():
    with pytest.raises(ValidationError, match="dialogue metadata"):
        _adapter.validate_python(
            {
                "kind": "reading",
                "spans": [_txt("Hei")],
                "translation": "Hello",
                "speaker_id": "anna",
            }
        )


def test_reading_block_given_character_without_dialogue_metadata_expect_validation_error():
    with pytest.raises(ValidationError, match="character_id requires"):
        _adapter.validate_python(
            {
                "kind": "reading",
                "spans": [_txt("Hei")],
                "translation": "Hello",
                "character_id": "anna",
            }
        )


def test_reading_block_given_voice_profile_expect_authored_profile_preserved():
    blk = _adapter.validate_python(
        {
            "kind": "reading",
            "spans": [_txt("Skal du til Lillehammer?")],
            "translation": "Are you going to Lillehammer?",
            "dialogue_id": "station_help",
            "speaker_id": "erik",
            "speaker_name": "Erik",
            "voice_profile": "masculine",
        }
    )

    assert blk.voice_profile == "masculine"


def test_reading_block_given_unknown_voice_profile_expect_validation_error():
    with pytest.raises(ValidationError, match="voice_profile must be one of"):
        _adapter.validate_python(
            {
                "kind": "reading",
                "spans": [_txt("Hei")],
                "translation": "Hello",
                "dialogue_id": "d1",
                "speaker_id": "erik",
                "speaker_name": "Erik",
                "voice_profile": "robotic",
            }
        )


def test_reading_block_given_voice_profile_without_dialogue_metadata_expect_validation_error():
    with pytest.raises(ValidationError, match="voice_profile requires"):
        _adapter.validate_python(
            {
                "kind": "reading",
                "spans": [_txt("Hei")],
                "translation": "Hello",
                "voice_profile": "masculine",
            }
        )


def test_examples_block_given_items_without_kind_expect_parsed():
    blk = _adapter.validate_python(
        {
            "kind": "examples",
            "items": [
                {
                    "no": [{"kind": "foreign_term", "value": "Huset er stort", "lang": "no"}],
                    "en": [_txt("The house is big")],
                }
            ],
        }
    )
    assert blk.items[0].no[0].value == "Huset er stort"
