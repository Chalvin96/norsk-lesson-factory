"""Task E Step 1: ``author_metadata_objectives`` must preserve card-authored CEFR
and objectives while the LLM supplies only title/goal.

Entry point: ``author_metadata_objectives``.
"""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.pipeline.cold_author.metadata_objectives import author_metadata_objectives
from lesson_builder.pipeline.cold_author.models import StageFailure, StageOK
from lesson_builder.pipeline.concept_requirements import ConceptRequirements


def _requirements() -> ConceptRequirements:
    return ConceptRequirements.model_validate(
        {
            "slug": "adjective_agreement",
            "cefr_level": "A1",
            "objectives": [
                {
                    "id": "obj_indefinite_forms",
                    "statement": "Recognize forms.",
                    "bloom_targets": ["understand"],
                },
                {
                    "id": "obj_production",
                    "statement": "Produce forms.",
                    "bloom_targets": ["apply"],
                },
            ],
            "required_anchor_forms": ["en fin bil"],
            "notes": "teach agreement",
            "min_clean_examples": 5,
        }
    )


class _FakeAgent:
    def __init__(self, response: str) -> None:
        self._response = response

    def invoke(self, prompt: str, **kw: Any) -> str:
        return self._response


def test_author_metadata_objectives_given_card_authored_objectives_expect_preserved_ids_and_cefr():
    result = author_metadata_objectives(
        "adjective_agreement",
        _requirements(),
        author_agent=_FakeAgent(
            json.dumps(
                {"title": "Adjective agreement", "goal": "Learn indefinite adjective agreement."},
                ensure_ascii=False,
            )
        ),
    )

    assert isinstance(result, StageOK)
    metadata = result.payload.model_dump(mode="json")
    assert metadata["cefr_level"] == "A1"
    assert metadata["objectives"] == _requirements().model_dump(mode="json")["objectives"]


def test_author_metadata_objectives_given_invalid_author_payload_expect_failure():
    result = author_metadata_objectives(
        "adjective_agreement",
        _requirements(),
        author_agent=_FakeAgent(json.dumps({"title": "T"}, ensure_ascii=False)),
    )

    assert isinstance(result, StageFailure)
    assert result.stage == "metadata_objectives"
