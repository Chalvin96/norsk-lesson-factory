"""Tests for terminology model invariants and queries.

Not a check itself — this module tests the typed terminology contracts.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry


def test_terminology_registry_given_duplicate_ids_expect_validation_error():
    data = _registry_data()
    data["concepts"].append({"id": "finite-verb", "preferred_label": "other"})
    with pytest.raises(ValidationError, match="duplicate"):
        TerminologyRegistry.model_validate(data)


def test_terminology_registry_given_ambiguous_labels_expect_validation_error():
    data = _registry_data()
    data["concepts"].append({"id": "other", "preferred_label": "finite verb"})
    with pytest.raises(ValidationError, match="ambiguous"):
        TerminologyRegistry.model_validate(data)


def test_find_concept_by_id_given_existing_id_expect_concept():
    registry = TerminologyRegistry.model_validate(_registry_data())
    assert registry.find_concept_by_id("finite-verb").preferred_label == "finite verb"


def test_find_concept_by_id_given_unknown_id_expect_none():
    registry = TerminologyRegistry.model_validate(_registry_data())
    assert registry.find_concept_by_id("unknown") is None


def test_collect_concepts_given_scopes_expect_partitioned_concepts():
    registry = TerminologyRegistry.model_validate(_registry_data())
    assert [concept.id for concept in registry.collect_active_concepts()] == ["finite-verb"]
    assert [concept.id for concept in registry.collect_reference_concepts()] == ["demonstrative"]


def test_terminology_registry_given_concepts_expect_banned_phrases_collected():
    registry = TerminologyRegistry.model_validate(_registry_data())
    assert registry.banned_phrases == ["tense-carrying verb"]


def test_terminology_registry_given_no_prose_tells_expect_empty_phrases():
    registry = TerminologyRegistry.model_validate(_registry_data())
    assert registry.prose_tell_phrases == []


def _registry_data() -> dict:
    return {
        "schema_version": "1",
        "concepts": [
            {
                "id": "finite-verb",
                "preferred_label": "finite verb",
                "scope": "active",
                "alternative_labels": ["verb that shows tense"],
                "forbidden_phrases": [{"phrase": "tense-carrying verb"}],
            },
            {"id": "demonstrative", "preferred_label": "demonstrative", "scope": "reference"},
        ],
    }
