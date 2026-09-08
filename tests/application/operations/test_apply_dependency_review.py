"""Behavior tests for applying a human-approved dependency graph."""

from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.apply_dependency_review import apply_dependency_review
from lesson_builder.application.operations.design_dependencies import run_dependency_design
from lesson_builder.domain.catalog.models import DependencyAssessment
from lesson_builder.domain.catalog.models import DependencyProposal
from lesson_builder.domain.catalog.models import DependencyProposalRequest
from lesson_builder.domain.catalog.models import DependencySecondOpinion
from lesson_builder.domain.catalog.models import DependencySecondOpinionFinding
from lesson_builder.domain.catalog.models import DependencySecondOpinionRequest


def _write_catalog(root: Path) -> None:
    path = root / "content" / "catalog" / "approved" / "catalog.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "catalog_status": "complete",
                "snapshot": "test",
                "entries": [
                    {"id": "first", "catalog_kind": "grammar", "title": "First", "cefr_tags": ["A1"]},
                    {"id": "second", "catalog_kind": "grammar", "title": "Second", "cefr_tags": ["A2"]},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_apply_dependency_review_given_human_approval_expect_schema_three_catalog(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path)

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
        return DependencySecondOpinion(summary="checked")

    review = run_dependency_design(
        repo_root=tmp_path,
        run_id="dependency-promote-source",
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )
    approval_path = tmp_path / "approval.yaml"
    approval_path.write_text(
        yaml.safe_dump(
            {
                "status": "approved",
                "approved_by": "human",
                "approved_at": "2026-08-10",
                "review_run": review.review.run_id,
                "source_catalog_hash": review.review.source_catalog_hash,
                "decisions": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = apply_dependency_review(
        repo_root=tmp_path,
        review_path=tmp_path / review.review_path,
        approval_path=approval_path,
    )

    promoted = yaml.safe_load(
        (tmp_path / "content" / "catalog" / "approved" / "catalog.yaml").read_text(encoding="utf-8")
    )
    assert result.status == "promoted"
    assert promoted["schema_version"] == 3
    assert promoted["dependency_graph"]["status"] == "complete"
    assert promoted["entries"][1]["prerequisites"] == ["first"]
    assert promoted["entries"][1]["helpful_prerequisites"] == []


def _review_with_p1_second_opinion(tmp_path: Path, *, run_id: str, with_finding: bool = True):
    """Build one dependency review with a P1 second opinion on first -> second."""

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
        findings = []
        if with_finding:
            findings.append(
                DependencySecondOpinionFinding(
                    prerequisite_id="first",
                    dependent_id="second",
                    current_kind="required",
                    severity="P1",
                    recommendation="reject",
                    rationale="The edge duplicates a helpful relationship.",
                )
            )
        return DependencySecondOpinion(summary="checked", findings=findings)

    return run_dependency_design(
        repo_root=tmp_path,
        run_id=run_id,
        proposal_service=proposal,
        second_opinion_service=second_opinion,
    )


def _write_approval(tmp_path: Path, review, decisions: list[dict]) -> Path:
    approval_path = tmp_path / "approval.yaml"
    approval_path.write_text(
        yaml.safe_dump(
            {
                "status": "approved",
                "approved_by": "human",
                "approved_at": "2026-08-21",
                "review_run": review.review.run_id,
                "source_catalog_hash": review.review.source_catalog_hash,
                "decisions": decisions,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return approval_path


def test_apply_dependency_review_given_approved_removal_of_reviewed_edge_expect_edge_removed(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path)
    review = _review_with_p1_second_opinion(tmp_path, run_id="dependency-approved-removal")
    assert "second-opinion severity requires human review: P1 first -> second" in review.review.unresolved

    result = apply_dependency_review(
        repo_root=tmp_path,
        review_path=tmp_path / review.review_path,
        approval_path=_write_approval(
            tmp_path,
            review,
            [
                {
                    "action": "remove_required_edge",
                    "prerequisite_id": "first",
                    "dependent_id": "second",
                    "kind": "required",
                }
            ],
        ),
    )

    promoted = yaml.safe_load(
        (tmp_path / "content" / "catalog" / "approved" / "catalog.yaml").read_text(encoding="utf-8")
    )
    assert result.status == "promoted"
    assert promoted["entries"][1]["prerequisites"] == []
    assert all(
        edge["prerequisite_id"] != "first" or edge["dependent_id"] != "second"
        for edge in promoted["dependency_graph"]["required_edges"]
    )


def test_apply_dependency_review_given_removal_without_second_opinion_finding_expect_refusal(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path)
    review = _review_with_p1_second_opinion(tmp_path, run_id="dependency-unreviewed-removal", with_finding=False)

    with pytest.raises(ValueError, match="did not flag for human review"):
        apply_dependency_review(
            repo_root=tmp_path,
            review_path=tmp_path / review.review_path,
            approval_path=_write_approval(
                tmp_path,
                review,
                [
                    {
                        "action": "remove_required_edge",
                        "prerequisite_id": "first",
                        "dependent_id": "second",
                        "kind": "required",
                    }
                ],
            ),
        )


def test_apply_dependency_review_given_removal_of_absent_edge_expect_refusal(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    review = _review_with_p1_second_opinion(tmp_path, run_id="dependency-absent-removal")
    review_path = tmp_path / review.review_path
    payload = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    payload["proposed_edges"] = []
    review_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="absent from the proposed graph"):
        apply_dependency_review(
            repo_root=tmp_path,
            review_path=review_path,
            approval_path=_write_approval(
                tmp_path,
                review,
                [
                    {
                        "action": "remove_required_edge",
                        "prerequisite_id": "first",
                        "dependent_id": "second",
                        "kind": "required",
                    }
                ],
            ),
        )


def test_apply_dependency_review_given_severity_item_without_removal_decision_expect_refusal(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path)
    review = _review_with_p1_second_opinion(tmp_path, run_id="dependency-missing-removal")

    with pytest.raises(ValueError, match="one explicit removal decision"):
        apply_dependency_review(
            repo_root=tmp_path,
            review_path=tmp_path / review.review_path,
            approval_path=_write_approval(tmp_path, review, []),
        )
