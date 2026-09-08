"""Behavior tests for curriculum dependency design runs."""

from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.design_dependencies import rerun_dependency_second_opinion
from lesson_builder.application.operations.design_dependencies import run_dependency_design
from lesson_builder.domain.catalog.models import DependencyAssessment
from lesson_builder.domain.catalog.models import DependencyProposal
from lesson_builder.domain.catalog.models import DependencyProposalRequest
from lesson_builder.domain.catalog.models import DependencySecondOpinion
from lesson_builder.domain.catalog.models import DependencySecondOpinionFinding
from lesson_builder.domain.catalog.models import DependencySecondOpinionRequest


def _write_catalog(root: Path, entries: list[dict[str, object]]) -> None:
    path = root / "content" / "catalog" / "approved" / "catalog.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "catalog_status": "complete",
                "snapshot": "test",
                "summary": {"deleted_owner_ids": ["deleted_owner"]},
                "entries": entries,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_run_dependency_design_given_partial_model_review_expect_scratch_exception_report(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del repo_root
        assert len(request.owners) == 2
        return DependencyProposal(
            assessments=[
                DependencyAssessment(
                    owner_id="second",
                    required_prerequisites=["first"],
                    status="reviewed",
                    rationale="The second owner assumes the first.",
                )
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-partial",
        proposal_service=proposal,
    )

    assert result.review.status == "needs_human_review"
    assert "missing assessment: first" in result.review.unresolved
    assert result.review.proposed_edges[0].prerequisite_id == "first"
    assert (tmp_path / result.review_path).is_file()
    readme = (tmp_path / result.review_path).with_name("README.md").read_text(encoding="utf-8")
    assert "Unresolved items" in readme


def test_run_dependency_design_given_complete_model_review_expect_ready_scratch_report(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(
            assessments=[
                DependencyAssessment(owner_id="first", status="reviewed"),
                DependencyAssessment(
                    owner_id="second",
                    required_prerequisites=["first"],
                    status="reviewed",
                ),
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-complete",
        proposal_service=proposal,
    )

    assert result.review.status == "ready_for_promotion"
    assert result.review.reviewed_owner_count == 2
    assert result.review.validation_errors == []


def test_run_dependency_design_given_p1_second_opinion_finding_expect_parked_review(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(
            assessments=[
                DependencyAssessment(owner_id="first", status="reviewed"),
                DependencyAssessment(owner_id="second", required_prerequisites=["first"], status="reviewed"),
            ]
        )

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        return DependencySecondOpinion(
            findings=[
                DependencySecondOpinionFinding(
                    prerequisite_id="first",
                    dependent_id="second",
                    current_kind="required",
                    severity="P1",
                    recommendation="keep",
                    rationale="The edge is foundational.",
                )
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-p1-finding",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "needs_human_review"
    assert "second-opinion severity requires human review: P1 first -> second" in result.review.unresolved


def test_run_dependency_design_given_redundant_p1_add_expect_advisory(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(
            assessments=[
                DependencyAssessment(owner_id="first", status="reviewed"),
                DependencyAssessment(owner_id="second", required_prerequisites=["first"], status="reviewed"),
            ]
        )

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        return DependencySecondOpinion(
            findings=[
                DependencySecondOpinionFinding(
                    prerequisite_id="first",
                    dependent_id="second",
                    current_kind="required",
                    severity="P1",
                    recommendation="add_required",
                    rationale="The edge should be required.",
                )
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-redundant-p1-add",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "ready_for_promotion"
    assert result.review.unresolved == []
    assert result.review.second_opinion is not None
    assert result.review.second_opinion.findings[0].severity == "P2"


def test_rerun_dependency_second_opinion_given_unchanged_review_expect_replaced_failure(
    tmp_path: Path,
) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first", status="reviewed")])

    initial = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-retry-source",
        proposal_service=proposal,
    )

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        return DependencySecondOpinion(summary="Retry completed")

    retried = rerun_dependency_second_opinion(
        repo_root=tmp_path,
        source_review_path=tmp_path / initial.review_path,
        run_id="dependency-retry-result",
        second_opinion_service=second_opinion,
    )

    assert retried.review.status == "ready_for_promotion"
    assert retried.review.second_opinion is not None
    assert retried.review.second_opinion.status == "completed"
    assert retried.review.second_opinion.summary == "Retry completed"
    assert retried.review.run_id == "dependency-retry-result"


def test_run_dependency_design_given_second_opinion_absent_keep_expect_graph_exception(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(
            assessments=[
                DependencyAssessment(owner_id="first", status="reviewed"),
                DependencyAssessment(owner_id="second", status="reviewed"),
            ]
        )

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        return DependencySecondOpinion(
            findings=[
                DependencySecondOpinionFinding(
                    prerequisite_id="first",
                    dependent_id="second",
                    recommendation="keep",
                    rationale="The graph should contain this edge.",
                )
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-absent-keep",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "needs_human_review"
    assert "second-opinion keep references absent edge: first -> second" in result.review.unresolved


def test_run_dependency_design_given_second_opinion_expect_persisted_advisory_findings(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [
            {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
            {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
        ],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(
            assessments=[
                DependencyAssessment(owner_id="first", status="reviewed"),
                DependencyAssessment(
                    owner_id="second",
                    required_prerequisites=["first"],
                    status="reviewed",
                ),
            ]
        )

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del repo_root
        assert len(request.owners) == 2
        assert request.proposed_edges[0].prerequisite_id == "first"
        return DependencySecondOpinion(
            summary="The required edge is plausible.",
            findings=[
                DependencySecondOpinionFinding(
                    prerequisite_id="first",
                    dependent_id="second",
                    current_kind="required",
                    severity="P2",
                    recommendation="keep",
                    rationale="The second lesson assumes the first outcome.",
                )
            ],
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-second-opinion",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "ready_for_promotion"
    assert result.review.second_opinion is not None
    assert result.review.second_opinion.status == "completed"
    assert result.review.second_opinion.findings[0].recommendation == "keep"
    readme = (tmp_path / result.review_path).with_name("README.md").read_text(encoding="utf-8")
    assert "Second opinion" in readme


def test_run_dependency_design_given_second_opinion_failure_expect_parked_review(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first", status="reviewed")])

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        raise RuntimeError("DeepSeek unavailable")

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-second-opinion-failed",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "needs_human_review"
    assert result.review.second_opinion is not None
    assert result.review.second_opinion.status == "failed"
    assert any("DeepSeek unavailable" in item for item in result.review.unresolved)


def test_run_dependency_design_given_unresolved_second_opinion_endpoint_expect_review_exception(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first", status="reviewed")])

    def second_opinion(
        request: DependencySecondOpinionRequest,
        *,
        repo_root: Path,
    ) -> DependencySecondOpinion:
        del request, repo_root
        return DependencySecondOpinion(
            findings=[
                DependencySecondOpinionFinding(
                    prerequisite_id="deleted_owner",
                    dependent_id="first",
                    recommendation="needs_human_review",
                    rationale="The deleted owner needs a replacement decision.",
                )
            ]
        )

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-second-opinion-unresolved",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )

    assert result.review.status == "needs_human_review"
    assert any("second-opinion unresolved prerequisite endpoint" in item for item in result.review.unresolved)
    assert any("second-opinion requires human review" in item for item in result.review.unresolved)


def test_run_dependency_design_given_model_review_flag_expect_human_review_status(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first", status="needs_human_review")])

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-model-review",
        proposal_service=proposal,
    )

    assert result.review.status == "needs_human_review"
    assert result.review.unresolved == ["assessment requires human review: first"]


def test_run_dependency_design_given_live_service_failure_expect_failed_scratch_review(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        raise RuntimeError("provider unavailable")

    result = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-failed",
        proposal_service=proposal,
    )

    assert result.review.status == "failed"
    assert result.review.source_catalog == "content/catalog/approved/catalog.yaml"
    assert "provider unavailable" in result.review.validation_errors[0]


def test_run_dependency_design_given_relative_repo_root_expect_catalog_resolved_once(
    tmp_path: Path, monkeypatch: object
) -> None:
    repo = tmp_path / "repo"
    _write_catalog(repo, [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}])
    monkeypatch.chdir(tmp_path)

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first")])

    result = run_dependency_design(repo_root=Path("repo"), run_id="relative-root", proposal_service=proposal)

    assert result.review.source_catalog == "content/catalog/approved/catalog.yaml"
    assert (repo / result.review_path).is_file()


def test_run_dependency_design_given_explicit_catalog_override_expect_root_relative_path(tmp_path: Path) -> None:
    _write_catalog(tmp_path, [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}])
    custom = tmp_path / "custom-catalog.yaml"
    custom.write_text(
        (tmp_path / "content/catalog/approved/catalog.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )

    def proposal(request: DependencyProposalRequest, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first")])

    result = run_dependency_design(
        repo_root=tmp_path,
        catalog_path=Path("custom-catalog.yaml"),
        run_id="explicit-catalog",
        proposal_service=proposal,
    )

    assert result.review.source_catalog == "custom-catalog.yaml"


def test_run_dependency_design_given_existing_run_id_expect_no_overwrite(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        [{"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]}],
    )

    def proposal(request: object, *, repo_root: Path) -> DependencyProposal:
        del request, repo_root
        return DependencyProposal(assessments=[DependencyAssessment(owner_id="first")])

    run_dependency_design(repo_root=tmp_path, run_id="dependency-once", proposal_service=proposal)

    with pytest.raises(FileExistsError):
        run_dependency_design(repo_root=tmp_path, run_id="dependency-once", proposal_service=proposal)
