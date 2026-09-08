"""Behavior tests for the catalog-design LangGraph."""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from lesson_builder.workflow.catalog_design.graph import build_catalog_graph
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.runner import run_catalog
from tests.workflow.catalog_design.fakes import FakeCatalogServices


def test_catalog_graph_given_two_discovery_branches_expect_merged_accept_and_existing_rejection():
    services = FakeCatalogServices(
        explorer_candidates=[
            _candidate("appointment_forms", "Making appointments", "explorer"),
            _candidate("short_answers", "Short answers", "explorer"),
        ],
        reviewer_candidates=[_candidate("appointment_forms", "Making appointments", "reviewer")],
        coverage_complete=True,
    )
    result = _invoke(
        services,
        existing_lessons=[
            ExistingLesson(
                slug="answering_ja_nei_jo",
                title="Answering ja, nei, and jo",
                aliases=["short_answers"],
            )
        ],
    )

    statuses = {decision["status"] for decision in result["proposal"]["decisions"]}
    assert statuses == {"merged", "rejected_existing"}
    merged = next(item for item in result["proposal"]["decisions"] if item["status"] == "merged")
    assert set(merged["candidate_ids"]) == {"explorer:appointment_forms", "reviewer:appointment_forms"}
    assert services.discovery_calls == {"explorer": 1, "reviewer": 1}


def test_catalog_graph_given_explorer_candidate_without_evidence_expect_acceptance():
    services = FakeCatalogServices(
        explorer_candidates=[_candidate("appointment_forms", "Making appointments", "explorer")],
        reviewer_candidates=[],
        coverage_complete=True,
    )

    result = _invoke(services)

    assert result["proposal"]["status"] == "ready"
    assert result["proposal"]["decisions"][0]["status"] == "accepted"


def test_catalog_graph_given_missing_novelty_proof_expect_structural_rejection():
    candidate = _candidate("appointment_forms", "Making appointments", "explorer").model_copy(
        update={"independent_difference": ""}
    )
    services = FakeCatalogServices(
        explorer_candidates=[candidate],
        reviewer_candidates=[],
        coverage_complete=True,
    )

    result = _invoke(services)

    assert result["proposal"]["decisions"][0]["status"] == "rejected_invalid"
    assert "independent_difference" in result["proposal"]["decisions"][0]["reason"]


def test_catalog_graph_given_existing_lesson_extension_expect_teaching_point_decision():
    services = FakeCatalogServices(
        explorer_candidates=[_candidate("weather_det", "Use det in weather sentences", "explorer")],
        reviewer_candidates=[],
        coverage_complete=True,
        resolution_slug="weather_det",
        resolution_title="Use det in weather sentences",
        resolution_relationship="lesson_extension",
        resolution_existing_slug="det_weather",
        resolution_teaching_point="Use det in simple weather sentences.",
    )

    result = _invoke(
        services,
        existing_lessons=[
            ExistingLesson(
                slug="det_er_vs_det_finnes",
                title="Choose det er or det finnes",
                aliases=["det_weather"],
            )
        ],
    )

    decision = result["proposal"]["decisions"][0]
    assert decision["status"] == "accepted"
    assert decision["relationship"] == "lesson_extension"
    assert decision["target_lesson_slug"] == "det_er_vs_det_finnes"
    assert decision["teaching_point"] == "Use det in simple weather sentences."


def test_catalog_graph_given_disjoint_merge_cores_expect_human_split_review():
    first = _candidate("appointment_forms", "Make an appointment", "explorer")
    second = _candidate("past_habits", "Talk about past habits", "reviewer").model_copy(
        update={"teachable_core": "Choose pleide å for repeated past habits."}
    )
    services = FakeCatalogServices(
        explorer_candidates=[first],
        reviewer_candidates=[second],
        coverage_complete=True,
    )

    result = _invoke(services)

    decision = result["proposal"]["decisions"][0]
    assert decision["status"] == "needs_human_review"
    assert "disjoint teachable cores" in decision["reason"]


def test_catalog_graph_given_unknown_nearest_reference_expect_structural_rejection():
    candidate = _candidate("appointment_forms", "Making appointments", "explorer").model_copy(
        update={"nearest_existing": ["invented_lesson_slug"]}
    )
    services = FakeCatalogServices(
        explorer_candidates=[candidate],
        reviewer_candidates=[],
        coverage_complete=True,
    )

    result = _invoke(services)

    assert result["proposal"]["decisions"][0]["status"] == "rejected_invalid"
    assert "unknown nearest_existing" in result["proposal"]["decisions"][0]["reason"]


def test_catalog_graph_given_soft_quality_dimension_expect_human_review():
    services = FakeCatalogServices(
        explorer_candidates=[_candidate("appointment_forms", "Making appointments", "explorer")],
        reviewer_candidates=[],
        coverage_complete=True,
        quality_score=0.9,
        dimension_scores={"distinctness": 0.4},
    )

    result = _invoke(services)

    assert result["proposal"]["decisions"][0]["status"] == "needs_human_review"


def test_catalog_graph_given_internal_title_jargon_expect_structural_rejection():
    candidate = _candidate("weather_det", "Dummy det in weather", "explorer")
    services = FakeCatalogServices(
        explorer_candidates=[candidate],
        reviewer_candidates=[],
        coverage_complete=True,
    )

    result = _invoke(services)

    assert result["proposal"]["decisions"][0]["status"] == "rejected_invalid"
    assert "internal jargon" in result["proposal"]["decisions"][0]["reason"]


def test_catalog_graph_given_explorer_failure_and_reviewer_candidate_expect_partial_result():
    services = FakeCatalogServices(
        explorer_candidates=[],
        reviewer_candidates=[_candidate("appointment_forms", "Making appointments", "reviewer")],
        coverage_complete=True,
        explorer_error=RuntimeError("quota temporarily unavailable"),
    )

    result = _invoke(services)

    assert result["proposal"]["status"] == "partial"
    assert any(item["status"] == "accepted" for item in result["proposal"]["decisions"])
    assert any("Explorer discovery failed" in error for error in result["proposal"]["errors"])


def test_catalog_graph_given_two_stagnant_iterations_expect_advice_once():
    services = FakeCatalogServices(
        explorer_candidates=[_candidate("appointment_forms", "Making appointments", "explorer")],
        reviewer_candidates=[],
        coverage_complete=False,
    )
    result = _invoke(services, max_iterations=3)

    assert services.advice_calls == 1
    assert services.discovery_calls == {"explorer": 3, "reviewer": 3}
    assert result["proposal"]["status"] == "partial"
    assert result["proposal"]["advice"] == "narrow the category boundary"
    assert result["proposal"]["stagnation_count"] >= 2


def test_catalog_runner_given_scratch_run_expect_no_canonical_files_changed(tmp_path: Path):
    services = FakeCatalogServices(
        explorer_candidates=[_candidate("appointment_forms", "Making appointments", "explorer")],
        reviewer_candidates=[],
        coverage_complete=True,
    )

    result = run_catalog(
        "communicative topics",
        cefr_tags=["A1"],
        max_iterations=1,
        repo_root=tmp_path,
        run_id="scratch-test",
        deps=services.deps(),
    )

    assert result["proposal"]["status"] == "ready"
    assert result["thread_id"] == "catalog:v2:communicative_topics:scratch-test"
    assert Path(result["proposal_path"]).is_file()
    assert not (tmp_path / "curriculum").exists()
    assert not (tmp_path / "data" / "lessons").exists()
    assert not (tmp_path / "dist" / "lessons").exists()


def test_catalog_runner_given_existing_failed_proposal_expect_preserves_artifact(tmp_path: Path):
    proposal_path = (
        tmp_path / "store" / "scratch" / "catalog" / "communicative_topics" / "prior-failure" / "proposal.json"
    )
    proposal_path.parent.mkdir(parents=True)
    original = '{"status":"failed","errors":["kept for debugging"]}\n'
    proposal_path.write_text(original, encoding="utf-8")

    with pytest.raises(FileExistsError, match="choose a new run_id"):
        run_catalog(
            "communicative topics",
            max_iterations=1,
            repo_root=tmp_path,
            run_id="prior-failure",
            deps=FakeCatalogServices(explorer_candidates=[], reviewer_candidates=[], coverage_complete=True).deps(),
        )

    assert proposal_path.read_text(encoding="utf-8") == original


def _invoke(
    services: FakeCatalogServices,
    *,
    existing_lessons: list[ExistingLesson] | None = None,
    max_iterations: int = 1,
    category: str = "communicative topics",
) -> dict[str, object]:
    graph = build_catalog_graph(services.deps(), checkpointer=MemorySaver())
    state = {
        "request": CatalogRequest(
            category=category,
            max_iterations=max_iterations,
        ).model_dump(mode="json"),
        "existing_lessons": [item.model_dump(mode="json") for item in (existing_lessons or [])],
        "repo_root": ".",
        "errors": [],
    }
    return graph.invoke(state, config={"configurable": {"thread_id": "catalog-test"}})


def _candidate(
    slug: str,
    title: str,
    source: str,
    *,
    category: str = "communicative topics",
) -> CatalogCandidate:
    return CatalogCandidate(
        candidate_id="raw",
        slug=slug,
        title=title,
        category=category,
        learner_question="How do I make an appointment in Norwegian?",
        scope="appointment requests, availability, and confirmation",
        out_of_scope=["the full healthcare vocabulary inventory"],
        rationale="It supports a focused, reusable learner interaction.",
        teachable_core="Choosing and confirming an appointment request frame.",
        independent_difference="The learner must complete a service-booking exchange rather than a generic request.",
        source_agent=source,  # type: ignore[arg-type]
    )
