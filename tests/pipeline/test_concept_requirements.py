"""Entry point: ``load_concept_requirements``.

Validates the typed concept-requirements contract used by cold-authoring and
the migration script.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.concept_requirements import load_concept_requirements


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
