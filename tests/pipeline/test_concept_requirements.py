"""Entry point: ``load_concept_requirements``.

Validates the typed concept-requirements contract used by cold-authoring and
the migration script.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.concept_requirements import (
    is_placeholder_objective,
    load_concept_requirements,
)


def _card(statement: str) -> dict:
    return {
        "slug": "word_order_main_clauses",
        "cefr_level": "A1",
        "objectives": [
            {"id": "o1", "statement": statement, "bloom_targets": ["understand"]}
        ],
        "required_anchor_forms": [],
        "notes": "n",
    }


def test_load_concept_requirements_given_valid_payload_expect_validated_objectives(tmp_path):
    path = tmp_path / "concept.json"
    path.write_text(
        json.dumps(
            {
                "slug": "adjective_agreement",
                "cefr_level": "A1",
                "objectives": [
                    {
                        "id": "obj_1",
                        "statement": "Recognize forms.",
                        "bloom_targets": ["understand"],
                    }
                ],
                "required_anchor_forms": ["en fin bil"],
                "notes": "teach agreement",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = load_concept_requirements(path)

    assert result.slug == "adjective_agreement"
    assert result.cefr_level == "A1"
    assert result.objectives[0].id == "obj_1"
    assert result.min_clean_examples is None


def test_load_concept_requirements_given_unknown_field_expect_validation_error(tmp_path):
    path = tmp_path / "concept.json"
    path.write_text(
        json.dumps(
            {
                "slug": "adjective_agreement",
                "cefr_level": "A1",
                "objectives": [
                    {
                        "id": "obj_1",
                        "statement": "Recognize forms.",
                        "bloom_targets": ["understand"],
                    }
                ],
                "required_anchor_forms": ["en fin bil"],
                "notes": "teach agreement",
                "unexpected": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_concept_requirements(path)


# ---------------------------------------------------------------------------
# Write-path placeholder guard (Phase 1 deferred bit)
# ---------------------------------------------------------------------------


def test_load_concept_requirements_rejects_placeholder_objective(tmp_path):
    # a card carrying a raw "Learn <slug>" objective must fail at load
    path = tmp_path / "concept.json"
    path.write_text(json.dumps(_card("Learn word_order_main_clauses")), encoding="utf-8")

    with pytest.raises(ValidationError, match="raw placeholder"):
        load_concept_requirements(path)


def test_load_concept_requirements_accepts_real_objective(tmp_path):
    # a real sentence is not a placeholder even when it starts with "Learn"
    path = tmp_path / "concept.json"
    path.write_text(
        json.dumps(_card("Learn main-clause word order with the verb second.")),
        encoding="utf-8",
    )

    result = load_concept_requirements(path)
    assert result.objectives[0].statement.startswith("Learn main-clause")


def test_is_placeholder_objective_predicate():
    assert is_placeholder_objective("Learn preterite")
    assert is_placeholder_objective("Learn word_order_main_clauses")
    assert not is_placeholder_objective("Learn the past tense properly.")
    assert not is_placeholder_objective("Recognize forms.")
