"""Entry point: ``FakeCatalogServices``.

Typed, offline collaborators for catalog graph behavior tests. They expose call
counters so routing behavior can be asserted without mocking subprocesses or
network calls.
"""

from __future__ import annotations

from collections.abc import Sequence

from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies
from lesson_builder.workflow.catalog_design.models import AdviceResult
from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import EvaluationBatch
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.models import ResolutionBatch


class FakeCatalogServices:
    """Fixed discovery/resolution/evaluation behavior for graph tests."""

    def __init__(
        self,
        *,
        explorer_candidates: list[CatalogCandidate],
        reviewer_candidates: list[CatalogCandidate],
        coverage_complete: bool,
        quality_score: float = 0.9,
        evaluation_status: str = "accept",
        advice_text: str = "narrow the category boundary",
        explorer_error: Exception | None = None,
        reviewer_error: Exception | None = None,
        evaluation_hard_failures: list[str] | None = None,
        resolution_slug: str = "appointment_forms",
        resolution_title: str = "Making appointments",
        resolution_relationship: str | None = None,
        resolution_existing_slug: str | None = None,
        resolution_teaching_point: str = "",
        dimension_scores: dict[str, float] | None = None,
    ) -> None:
        self.explorer_candidates = explorer_candidates
        self.reviewer_candidates = reviewer_candidates
        self.coverage_complete = coverage_complete
        self.quality_score = quality_score
        self.evaluation_status = evaluation_status
        self.advice_text = advice_text
        self.explorer_error = explorer_error
        self.reviewer_error = reviewer_error
        self.evaluation_hard_failures = evaluation_hard_failures or []
        self.resolution_slug = resolution_slug
        self.resolution_title = resolution_title
        self.resolution_relationship = resolution_relationship
        self.resolution_existing_slug = resolution_existing_slug
        self.resolution_teaching_point = resolution_teaching_point
        self.dimension_scores = dimension_scores or {}
        self.discovery_calls = {"explorer": 0, "reviewer": 0}
        self.resolve_calls = 0
        self.evaluate_calls = 0
        self.advice_calls = 0

    def deps(self) -> CatalogDesignDependencies:
        return CatalogDesignDependencies(
            discover_explorer=self.discover_explorer,
            discover_reviewer=self.discover_reviewer,
            resolve=self.resolve,
            evaluate=self.evaluate,
            advise=self.advise,
        )

    def discover_explorer(
        self, _request: CatalogRequest, _existing_lessons: Sequence[ExistingLesson] = ()
    ) -> CandidateBatch:
        self.discovery_calls["explorer"] += 1
        if self.explorer_error is not None:
            raise self.explorer_error
        return CandidateBatch(candidates=self.explorer_candidates)

    def discover_reviewer(
        self, _request: CatalogRequest, _existing_lessons: Sequence[ExistingLesson] = ()
    ) -> CandidateBatch:
        self.discovery_calls["reviewer"] += 1
        if self.reviewer_error is not None:
            raise self.reviewer_error
        return CandidateBatch(candidates=self.reviewer_candidates)

    def resolve(
        self,
        _request: CatalogRequest,
        candidates: list[CatalogCandidate],
        _existing_lessons: list[ExistingLesson],
        _advice: str | None,
    ) -> ResolutionBatch:
        self.resolve_calls += 1
        if not candidates:
            return ResolutionBatch()
        relationship = self.resolution_relationship or ("merge" if len(candidates) > 1 else "distinct")
        return ResolutionBatch(
            resolutions=[
                CandidateResolution(
                    canonical_slug=self.resolution_slug,
                    canonical_title=self.resolution_title,
                    candidate_ids=[candidate.candidate_id for candidate in candidates],
                    relationship=relationship,  # type: ignore[arg-type]
                    existing_slug=self.resolution_existing_slug,
                    teaching_point=self.resolution_teaching_point,
                    rationale="the candidates describe the same learner decision",
                    confidence=0.9,
                )
            ]
        )

    def evaluate(
        self,
        _request: CatalogRequest,
        _candidates: list[CatalogCandidate],
        _resolutions: list[CandidateResolution],
    ) -> EvaluationBatch:
        self.evaluate_calls += 1
        return EvaluationBatch(
            evaluations=[
                CandidateEvaluation(
                    canonical_slug=resolution.canonical_slug,
                    distinctness=self.dimension_scores.get("distinctness", self.quality_score),
                    usefulness=self.dimension_scores.get("usefulness", self.quality_score),
                    scope_clarity=self.dimension_scores.get("scope_clarity", self.quality_score),
                    category_fit=self.dimension_scores.get("category_fit", self.quality_score),
                    accuracy=self.dimension_scores.get("accuracy", self.quality_score),
                    quality_score=self.quality_score,
                    status=self.evaluation_status,  # type: ignore[arg-type]
                    hard_failures=self.evaluation_hard_failures,
                )
                for resolution in _resolutions
            ],
            coverage_complete=self.coverage_complete,
        )

    def advise(
        self,
        _request: CatalogRequest,
        _candidates: list[CatalogCandidate],
        _resolutions: list[CandidateResolution],
        _evaluations: list[CandidateEvaluation],
        _reason: str,
    ) -> AdviceResult:
        self.advice_calls += 1
        return AdviceResult(advice=self.advice_text)


__all__ = ["FakeCatalogServices"]
