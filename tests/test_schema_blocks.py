import pytest
from pydantic import TypeAdapter, ValidationError

from lesson_builder.schema.blocks import Block

_adapter = TypeAdapter(Block)


def _txt(v: str) -> dict:
    return {"kind": "text", "value": v}


def test_heading_parses():
    blk = _adapter.validate_python({"kind": "heading", "level": 3, "spans": [_txt("Forms")]})
    assert blk.level == 3


def test_heading_level_constrained():
    with pytest.raises(ValidationError):
        _adapter.validate_python({"kind": "heading", "level": 2, "spans": [_txt("x")]})


def test_table_col_langs_must_match_header_count():
    bad = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Indef")], [_txt("Def")]],  # 3 cols
        "rows": [],
    }
    with pytest.raises(ValidationError, match="col_langs"):
        _adapter.validate_python(bad)


def test_table_row_width_must_match_col_count():
    bad = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Form")]],
        "rows": [[[_txt("en")]]],  # 1 cell, expected 2
    }
    with pytest.raises(ValidationError, match="col_langs"):
        _adapter.validate_python(bad)


def test_well_formed_paradigm_table():
    ok = {
        "kind": "table",
        "col_langs": ["en", "no"],
        "headers": [[_txt("Gender")], [_txt("Indefinite")]],
        "rows": [[[_txt("neuter")], [{"kind": "foreign_term", "value": "stort", "lang": "no"}]]],
    }
    blk = _adapter.validate_python(ok)
    assert blk.col_langs == ["en", "no"]


def test_callout_nests_blocks():
    blk = _adapter.validate_python(
        {"kind": "callout", "level": "tip", "blocks": [{"kind": "paragraph", "spans": [_txt("hi")]}]}
    )
    assert blk.blocks[0].kind == "paragraph"


def test_examples_block_items_without_kind():
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
