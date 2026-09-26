"""Behavior tests for deterministic catalog service boundaries."""

from __future__ import annotations

import json

import pytest

from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.workflow.catalog_design.dependencies import default_discover_reviewer
from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from tests.clients.llm.fakes import FakeLlmClient


def test_catalog_candidate_given_evidence_field_expect_schema_rejection():
    with pytest.raises(ValueError, match="evidence"):
        CatalogCandidate.model_validate(
            {
                "candidate_id": "explorer:word_order",
                "slug": "word_order",
                "title": "Verb position",
                "category": "grammar",
                "learner_question": "Where does the finite verb go?",
                "scope": "finite verb position in a main clause",
                "rationale": "a recurring learner decision",
                "source_agent": "explorer",
                "evidence": [{"kind": "web", "claim": "the verb is second"}],
            }
        )


def test_candidate_batch_given_more_than_discovery_limit_expect_schema_rejection() -> None:
    candidates = [
        CatalogCandidate(
            candidate_id=f"reviewer:candidate-{index}",
            slug=f"candidate-{index}",
            title=f"Candidate {index}",
            category="grammar",
            learner_question="What decision does the learner make?",
            scope="one learner decision",
            rationale="a focused learner decision",
        )
        for index in range(9)
    ]

    with pytest.raises(ValueError, match="at most 8 items"):
        CandidateBatch(candidates=candidates)


def test_candidate_resolution_given_duplicate_candidate_ids_expect_schema_rejection() -> None:
    with pytest.raises(ValueError, match="candidate_ids must be unique"):
        CandidateResolution(
            canonical_slug="word_order",
            canonical_title="Choose word order",
            candidate_ids=["reviewer:word_order", "reviewer:word_order"],
            relationship="new",
            rationale="one learner decision",
            confidence=0.9,
        )


def test_catalog_discovery_given_successful_llm_expect_records_provenance(monkeypatch):
    payload = {
        "candidates": [
            {
                "candidate_id": "reviewer:negation",
                "slug": "negation",
                "title": "Simple negation",
                "category": "grammar",
                "learner_question": "Where does ikke go?",
                "scope": "simple main clauses",
                "rationale": "frequent learner decision",
            }
        ]
    }
    client = FakeLlmClient.responding(json.dumps(payload), name="opencode")
    agent = JobRunner(
        name="catalog_reviewer",
        client=client,
        model="openai/gpt-5.6-sol",
        agent="catalog-review",
        variant="xhigh",
    )
    monkeypatch.setattr("lesson_builder.workflow.catalog_design.dependencies.catalog_reviewer", lambda: agent)
    provenance: list[dict[str, object]] = []

    result = default_discover_reviewer(
        CatalogRequest(category="grammar"),
        provenance_sink=provenance,
    )

    assert result.candidates[0].title == "Simple negation"
    assert provenance[0]["stage"] == "discover_reviewer"
    assert provenance[0]["status"] == "ok"
    assert provenance[0]["model"] == "openai/gpt-5.6-sol"
    assert provenance[0]["agent"] == "catalog-review"
    assert provenance[0]["variant"] == "xhigh"


def test_catalog_discovery_given_llm_failure_expect_records_error_provenance(monkeypatch):
    client = FakeLlmClient.raising(BackendDownException("adapter unavailable"), name="opencode")
    agent = JobRunner(
        name="catalog_reviewer",
        client=client,
        model="openai/gpt-5.6-sol",
        agent="catalog-review",
        variant="xhigh",
    )
    monkeypatch.setattr("lesson_builder.workflow.catalog_design.dependencies.catalog_reviewer", lambda: agent)
    provenance: list[dict[str, object]] = []

    with pytest.raises(BackendDownException):
        default_discover_reviewer(CatalogRequest(category="grammar"), provenance_sink=provenance)

    assert provenance[0]["status"] == "error"
    assert provenance[0]["stage"] == "discover_reviewer"
    assert "adapter unavailable" in str(provenance[0]["error"])
