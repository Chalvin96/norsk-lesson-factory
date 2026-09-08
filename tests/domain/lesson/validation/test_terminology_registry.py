"""Tests for pure terminology registry parsing, rendering, and resolution."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.terminology import GlossaryConcept
from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry
from lesson_builder.domain.lesson.validation.terminology_registry import build_registry_version
from lesson_builder.domain.lesson.validation.terminology_registry import parse_terminology_registry
from lesson_builder.domain.lesson.validation.terminology_registry import render_concept_index
from lesson_builder.domain.lesson.validation.terminology_registry import render_prompt_context
from lesson_builder.domain.lesson.validation.terminology_registry import resolve_concept_ids


def test_parse_terminology_registry_given_ambiguous_preferred_labels_expect_validation_error():
    data = _valid_registry_data()
    data["concepts"].append(
        {
            "id": "other-concept",
            "preferred_label": "finite verb",
            "scope": "active",
        }
    )

    with pytest.raises(ValidationError, match="ambiguous"):
        parse_terminology_registry(data)


def test_parse_terminology_registry_given_alternative_label_collision_expect_validation_error():
    data = _valid_registry_data()
    data["concepts"][1]["alternative_labels"] = ["finite verb"]

    with pytest.raises(ValidationError, match="ambiguous"):
        parse_terminology_registry(data)


def test_parse_terminology_registry_given_unknown_scope_expect_validation_error():
    data = _valid_registry_data()
    data["concepts"][0]["scope"] = "draft"

    with pytest.raises(ValidationError, match="scope"):
        parse_terminology_registry(data)


# ---------------------------------------------------------------------------
# Prompt-context rendering
# ---------------------------------------------------------------------------


def test_render_prompt_context_given_reference_concept_id_expect_demonstrative_rendered():
    registry = parse_terminology_registry(_valid_registry_data())

    context = render_prompt_context(registry, concept_ids=["demonstrative"])

    assert "demonstrative" in context
    assert "demonstrativ" in context
    assert "demonstrative pronoun" in context


def test_render_prompt_context_given_active_only_expect_no_reference_concepts():
    registry = parse_terminology_registry(_valid_registry_data())

    context = render_prompt_context(registry)

    assert "finite verb" in context
    assert "noun phrase" in context
    assert "demonstrative:" not in context


def test_render_prompt_context_given_empty_registry_expect_fallback_message():
    registry = TerminologyRegistry(
        schema_version="1",
        concepts=[
            GlossaryConcept(id="x", preferred_label="x", scope="reference"),
        ],
    )

    context = render_prompt_context(registry)

    assert "No terminology concepts" in context


def test_render_concept_index_given_reference_concept_expect_selectable_id_without_full_guidance():
    registry = parse_terminology_registry(_valid_registry_data())

    index = render_concept_index(registry)

    assert "id=demonstrative; learner term=demonstrative (demonstrativ)" in index
    assert "avoid pointing word" not in index


# ---------------------------------------------------------------------------
# Concept ID resolution
# ---------------------------------------------------------------------------


def test_resolve_concept_ids_given_valid_ids_expect_resolved():
    registry = parse_terminology_registry(_valid_registry_data())

    resolved = resolve_concept_ids(registry, ["finite-verb", "demonstrative"])

    assert [concept.id for concept in resolved] == ["finite-verb", "demonstrative"]


def test_resolve_concept_ids_given_unknown_id_expect_value_error():
    registry = parse_terminology_registry(_valid_registry_data())

    with pytest.raises(ValueError, match="unknown terminology concept id"):
        resolve_concept_ids(registry, ["nonexistent-id"])


def test_resolve_concept_ids_given_duplicates_expect_de_duplicated():
    registry = parse_terminology_registry(_valid_registry_data())

    resolved = resolve_concept_ids(registry, ["finite-verb", "finite-verb"])

    assert len(resolved) == 1


# ---------------------------------------------------------------------------
# Unique concept count (CEFR budget)
# ---------------------------------------------------------------------------


def test_build_registry_version_given_same_content_expect_same_hash():
    data1 = _valid_registry_data()
    data2 = _valid_registry_data()

    registry1 = parse_terminology_registry(data1)
    registry2 = parse_terminology_registry(data2)

    assert build_registry_version(registry1) == build_registry_version(registry2)
    assert build_registry_version(registry1).startswith("sha256:")


def test_build_registry_version_given_different_content_expect_different_hash():
    data1 = _valid_registry_data()
    data2 = _valid_registry_data()
    data2["concepts"][0]["preferred_label"] = "changed label"

    registry1 = parse_terminology_registry(data1)
    registry2 = parse_terminology_registry(data2)

    assert build_registry_version(registry1) != build_registry_version(registry2)


def _valid_registry_data() -> dict:
    return {
        "schema_version": "1",
        "prose_tells": [
            {"phrase": "In this lesson, you will", "rationale": "formulaic opener"},
        ],
        "prose_tell_density": {"threshold": 2, "patterns": [", meaning", ", which means"]},
        "concepts": [
            {
                "id": "finite-verb",
                "preferred_label": "finite verb",
                "norwegian_label": "finitt verb",
                "alternative_labels": ["verb that shows tense"],
                "scaffold": "verb that shows tense",
                "forbidden_phrases": [
                    {"phrase": "tense-carrying verb", "rationale": "coined variant"},
                ],
                "scope": "active",
                "cefr_note": "A1",
                "guidance": "introduce then use consistently",
                "rationale": "corpus audit found fragmentation",
            },
            {
                "id": "demonstrative",
                "preferred_label": "demonstrative",
                "norwegian_label": "demonstrativ",
                "alternative_labels": ["demonstrative pronoun", "demonstrative determiner"],
                "scope": "reference",
                "cefr_note": "A2",
                "guidance": "avoid pointing word",
            },
            {
                "id": "noun-phrase",
                "preferred_label": "noun phrase",
                "alternative_labels": ["NP"],
                "forbidden_phrases": [
                    {"phrase": "noun group", "rationale": "corpus drift"},
                ],
                "scope": "active",
                "cefr_note": "A2",
            },
        ],
    }
